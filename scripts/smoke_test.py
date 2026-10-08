"""Phase 0 smoke test (Req. Section 11.3, step 6; Architecture Spec Section 0.3).

Checks that the pinned libraries work together and that the APIs the code
generation will target exist. Each check runs independently and prints PASS or
FAIL. Secrets are loaded from .env and never printed.

Run from the repository root:
    .venv\\Scripts\\python.exe scripts\\smoke_test.py      (Windows)
    .venv/bin/python scripts/smoke_test.py                (Linux / Vocareum)
"""

from __future__ import annotations

import operator
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Annotated, Callable, TypedDict

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SECRET_VARS = ("OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
REQUIRED_VARS = SECRET_VARS + ("OPENAI_BASE_URL",)

# LangFuse SDK versions read either name for the host; set both.
if os.getenv("LANGFUSE_HOST") and not os.getenv("LANGFUSE_BASE_URL"):
    os.environ["LANGFUSE_BASE_URL"] = os.environ["LANGFUSE_HOST"]
os.environ.setdefault("LANGFUSE_HOST", "https://cloud.langfuse.com")
# Keep DeepEval from sending its own usage telemetry.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
BASE_URL = os.getenv("OPENAI_BASE_URL") or None
# Every LLM call caps its output. Strict json_schema structured output can make
# gpt-4o-mini emit thousands of whitespace tokens; function calling avoids it.
MAX_OUTPUT_TOKENS = 1500
STRUCTURED_METHOD = "function_calling"


def make_llm(**overrides):
    """ChatOpenAI configured the way the project will use it."""
    from langchain_openai import ChatOpenAI

    params = dict(model=LLM_MODEL, base_url=BASE_URL, temperature=0, timeout=30,
                  max_retries=0, max_tokens=MAX_OUTPUT_TOKENS)
    params.update(overrides)
    return ChatOpenAI(**params)

results: list[tuple[str, bool, str]] = []
shared: dict[str, object] = {}


def scrub(text: str) -> str:
    """Remove any secret values from text before it is printed."""
    for name in SECRET_VARS:
        value = os.getenv(name)
        if value and len(value) > 6:
            text = text.replace(value, f"<{name}>")
    return text


def check(name: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Register a check; its return value is the detail shown on PASS."""

    def wrap(fn: Callable[[], str]) -> Callable[[], str]:
        def run() -> str:
            start = time.perf_counter()
            try:
                detail = fn()
                ok = True
            except Exception as exc:  # noqa: BLE001 - report every failure
                last = traceback.extract_tb(exc.__traceback__)[-1]
                detail = f"{type(exc).__name__}: {exc} (at {Path(last.filename).name}:{last.lineno})"
                ok = False
            elapsed = time.perf_counter() - start
            results.append((name, ok, f"{scrub(detail)} [{elapsed:.1f}s]"))
            return detail

        run.__name__ = fn.__name__
        CHECKS.append(run)
        return run

    return wrap


CHECKS: list[Callable[[], str]] = []


@check("Python version is 3.10")
def check_python() -> str:
    version = sys.version.split()[0]
    if sys.version_info[:2] != (3, 10):
        raise RuntimeError(f"running Python {version}; the project targets 3.10")
    return version


@check(".env provides the required settings")
def check_env() -> str:
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"missing in .env: {', '.join(missing)}")
    key_kind = "Vocareum gateway key" if os.environ["OPENAI_API_KEY"].startswith("voc-") else "OpenAI key"
    return f"all set ({key_kind}, base URL {BASE_URL}, LangFuse host {os.environ['LANGFUSE_HOST']})"


@check("ChatOpenAI structured output (function calling) through the base URL")
def check_chat() -> str:
    from pydantic import BaseModel, Field

    class Verdict(BaseModel):
        service: str = Field(description="The service name mentioned in the text")
        severity: str = Field(description="One of S1, S2, S3, S4")

    llm = make_llm()
    structured = llm.with_structured_output(Verdict, method=STRUCTURED_METHOD)
    out = structured.invoke(
        "payments-service error rate is 38% and customers cannot transfer funds. "
        "Return the service name and severity S1."
    )
    if not isinstance(out, Verdict) or not out.service:
        raise RuntimeError(f"unexpected structured output: {out!r}")
    shared["llm"] = llm
    return f"model={LLM_MODEL}, got service={out.service!r} severity={out.severity!r}"


@check("Tool binding (bind_tools) returns a tool call")
def check_tools() -> str:
    from langchain_core.tools import tool

    @tool
    def query_logs(service: str, level: str) -> str:
        """Return log lines for a service at a log level."""
        return "stub"

    msg = make_llm().bind_tools([query_logs]).invoke("Fetch ERROR logs for payments-service using the tool.")
    calls = getattr(msg, "tool_calls", None) or []
    if not calls:
        raise RuntimeError("model returned no tool call")
    return f"tool_call={calls[0]['name']} args={calls[0]['args']}"


@check("OpenAI embeddings + FAISS IndexFlatIP search")
def check_embeddings_faiss() -> str:
    import faiss
    import numpy as np
    from langchain_openai import OpenAIEmbeddings

    docs = [
        "Rollback a failed release of payments-service (RB-PAY-003).",
        "Kafka consumer lag and rebalance storm handling (RB-KFK-001).",
        "Renew an expired TLS certificate on the payment gateway (RB-NET-004).",
    ]
    emb = OpenAIEmbeddings(model=EMBEDDING_MODEL, base_url=BASE_URL)
    vectors = np.array(emb.embed_documents(docs), dtype="float32")
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    query = np.array([emb.embed_query("certificate expired on card payment route")], dtype="float32")
    faiss.normalize_L2(query)
    scores, ids = index.search(query, 1)
    top = docs[int(ids[0][0])]
    if "RB-NET-004" not in top:
        raise RuntimeError(f"unexpected top result: {top}")
    return f"dim={vectors.shape[1]}, top hit RB-NET-004 (score {scores[0][0]:.2f})"


@check("Markdown + recursive text splitters keep section metadata")
def check_splitters() -> str:
    from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

    md = "# RB-PAY-003\n## 1 Symptoms\nErrors rise after release.\n## 2 Rollback\nRoll back to the previous version."
    sections = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "doc"), ("##", "section")]).split_text(md)
    chunks = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100).split_documents(sections)
    if not chunks or "section" not in chunks[-1].metadata:
        raise RuntimeError("section metadata missing")
    return f"{len(chunks)} chunks, last section={chunks[-1].metadata['section']!r}"


@check("LangGraph: parallel fan-out, join, merge reducer, branch to END")
def check_langgraph() -> str:
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        steps: Annotated[list[str], operator.add]
        level: str

    def rules(state: State) -> dict:
        return {"steps": ["rules"], "level": "S1"}

    def fast_page(state: State) -> dict:
        return {"steps": ["fast_page"]}

    def triage(state: State) -> dict:
        return {"steps": ["triage"]}

    def rca(state: State) -> dict:
        return {"steps": ["rca"]}

    def changes(state: State) -> dict:
        return {"steps": ["changes"]}

    def join(state: State) -> dict:
        return {"steps": ["join"]}

    g = StateGraph(State)
    for name, fn in [("rules", rules), ("fast_page", fast_page), ("triage", triage),
                     ("rca", rca), ("changes", changes), ("join", join)]:
        g.add_node(name, fn)
    g.add_edge(START, "rules")
    g.add_conditional_edges(
        "rules", lambda s: ["fast_page", "triage"] if s["level"] == "S1" else ["triage"],
        ["fast_page", "triage"],
    )
    g.add_edge("fast_page", END)
    g.add_edge("triage", "rca")
    g.add_edge("triage", "changes")
    g.add_edge(["rca", "changes"], "join")
    g.add_edge("join", END)
    out = g.compile().invoke({"steps": [], "level": ""})
    steps = out["steps"]
    expected = {"rules", "fast_page", "triage", "rca", "changes", "join"}
    if set(steps) != expected or steps.count("join") != 1:
        raise RuntimeError(f"unexpected steps: {steps}")
    if steps.index("fast_page") > steps.index("rca"):
        raise RuntimeError(f"fast_page did not run in the same step as triage: {steps}")
    return " -> ".join(steps)


@check("LangFuse: auth, trace with LangChain callback handler, score")
def check_langfuse() -> str:
    from langfuse import get_client

    client = get_client()
    if hasattr(client, "auth_check") and not client.auth_check():
        raise RuntimeError("auth_check() returned False")

    from langfuse.langchain import CallbackHandler

    handler = CallbackHandler()
    llm = shared.get("llm")
    if llm is None:
        raise RuntimeError("chat check failed earlier; no LLM to trace")

    span_api = next(
        (n for n in ("start_as_current_observation", "start_as_current_span") if hasattr(client, n)), None
    )
    if span_api is None:
        raise RuntimeError("no start_as_current_observation/start_as_current_span on client")
    with getattr(client, span_api)(name="phase0-smoke-test") as span:
        llm.invoke("Reply with the single word: traced", config={"callbacks": [handler]})
        trace_id = client.get_current_trace_id()
    if not trace_id:
        raise RuntimeError("no trace id")

    score_api = next((n for n in ("create_score", "score") if hasattr(client, n)), None)
    if score_api is None:
        raise RuntimeError("no create_score/score method")
    getattr(client, score_api)(name="smoke_test", value=1.0, trace_id=trace_id)
    client.flush()
    shared["trace_id"] = trace_id
    shared["langfuse"] = client
    shared["score_api"] = score_api
    return f"trace_id={trace_id} (APIs: {span_api}, {score_api})"


def make_judge():
    """DeepEval judge that uses function calling instead of strict json_schema.

    DeepEval's built-in OpenAI model requests strict structured output, which
    made gpt-4o-mini emit ~10k whitespace tokens on the Faithfulness verdict
    schema (98 s per call). This judge reuses the project's ChatOpenAI setup.
    """
    import asyncio

    from deepeval.models import DeepEvalBaseLLM

    class FunctionCallingJudge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            super().__init__(model=LLM_MODEL)

        def load_model(self):
            return make_llm(timeout=60)

        def generate(self, prompt: str, schema=None, **kwargs):
            if schema is None:
                return self.model.invoke(prompt).content
            return self.model.with_structured_output(schema, method=STRUCTURED_METHOD).invoke(prompt)

        async def a_generate(self, prompt: str, schema=None, **kwargs):
            return await asyncio.to_thread(self.generate, prompt, schema)

        def get_model_name(self) -> str:
            return f"{LLM_MODEL} (function calling)"

    return FunctionCallingJudge()


@check("DeepEval FaithfulnessMetric with the function-calling judge")
def check_deepeval() -> str:
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(
        input="What caused the payments outage?",
        actual_output="Release v2.14.0 reduced the DB timeout, causing payment failures.",
        retrieval_context=[
            "Deployment v2.14.0 of payments-service changed the DB client timeout from 5s to 1s.",
            "After the deployment, DB timeout errors and failed payments increased.",
        ],
    )
    metric = FaithfulnessMetric(model=make_judge(), threshold=0.5, async_mode=False, include_reason=False)
    metric.measure(case)
    score = float(metric.score)
    client = shared.get("langfuse")
    pushed = "not pushed (no LangFuse trace)"
    if client is not None and shared.get("trace_id"):
        getattr(client, shared["score_api"])(name="faithfulness", value=score, trace_id=shared["trace_id"])
        client.flush()
        pushed = "pushed to the LangFuse trace"
    return f"faithfulness={score:.2f}, {pushed}"


MCP_SERVER_SOURCE = '''
from mcp.server.fastmcp import FastMCP

server = FastMCP("smoke-tools")


@server.tool()
def query_logs(service: str, level: str = "ERROR") -> dict:
    """Return a log summary for a service at a log level."""
    return {"service": service, "level": level, "record_count": 3}


if __name__ == "__main__":
    server.run(transport="stdio")
'''


@check("MCP: stdio server, LangChain adapter, async + background-loop call, LLM tool choice")
def check_mcp() -> str:
    import asyncio
    import tempfile
    import threading

    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_file = Path(tempfile.mkdtemp()) / "smoke_mcp_server.py"
    server_file.write_text(MCP_SERVER_SOURCE, encoding="utf-8")
    client = MultiServerMCPClient(
        {"smoke": {"command": sys.executable, "args": [str(server_file)], "transport": "stdio"}}
    )

    # 1. Plain async use.
    async def list_and_call():
        tools = await client.get_tools()
        tool = next(t for t in tools if t.name == "query_logs")
        return tools, await tool.ainvoke({"service": "payments-service"})

    tools, async_result = asyncio.run(list_and_call())
    if "payments-service" not in str(async_result):
        raise RuntimeError(f"unexpected MCP tool result: {async_result!r}")

    # 2. Synchronous caller using a background event loop (the bridge the
    #    tool registry will use so graph nodes stay synchronous).
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        tool = next(t for t in tools if t.name == "query_logs")
        future = asyncio.run_coroutine_threadsafe(tool.ainvoke({"service": "accounts-service", "level": "WARN"}), loop)
        bridge_result = future.result(timeout=60)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
    if "accounts-service" not in str(bridge_result):
        raise RuntimeError(f"unexpected bridged MCP result: {bridge_result!r}")

    # 3. The LLM can choose an MCP-provided tool.
    msg = make_llm().bind_tools(tools).invoke("Get ERROR logs for payments-service using the tool.")
    calls = getattr(msg, "tool_calls", None) or []
    if not calls or calls[0]["name"] != "query_logs":
        raise RuntimeError(f"LLM did not call the MCP tool: {calls}")
    return f"tools={[t.name for t in tools]}, async and bridged calls OK, LLM chose {calls[0]['name']}"


@check("Streamlit, pandas, pdfplumber, JSON logger, YAML import and basic use")
def check_misc() -> str:
    import io
    import json
    import logging

    import pandas as pd
    import pdfplumber  # noqa: F401
    import streamlit  # noqa: F401
    import yaml

    try:
        from pythonjsonlogger.json import JsonFormatter
        api = "pythonjsonlogger.json.JsonFormatter"
    except ImportError:
        from pythonjsonlogger.jsonlogger import JsonFormatter
        api = "pythonjsonlogger.jsonlogger.JsonFormatter"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("smoke")
    logger.addHandler(handler)
    logger.propagate = False
    logger.warning("hello", extra={"incident_id": "INC-TEST"})
    record = json.loads(stream.getvalue())
    if record.get("incident_id") != "INC-TEST":
        raise RuntimeError("extra field missing from JSON log line")
    rules = yaml.safe_load("rules: [{id: R-S1-ERR, threshold: 15}]")
    frame = pd.DataFrame({"x": [1, 2]})
    return f"logger API {api}; yaml rules={len(rules['rules'])}; pandas rows={len(frame)}"


def main() -> int:
    print(f"Phase 0 smoke test — repo {ROOT.name}\n")
    for run in CHECKS:
        run()
    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    if "trace_id" in shared:
        print(f"Check the LangFuse UI for trace {shared['trace_id']} (name 'phase0-smoke-test').")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
