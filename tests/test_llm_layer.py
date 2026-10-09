"""LLM layer and prompts (Architecture Spec Sections 4.4, 4.5, 6.1)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from src.agent import llm, prompts
from src.errors import CostCapExceeded, RecoverableError
from src.schemas.analysis import TriageResult
from src.schemas.enums import ErrorType
from src.schemas.graph_state import initial_state
from src.schemas.enums import RunMode
from tests.fake_llm import FakeChatModel


def triage_answer(kind, schema, messages):
    return TriageResult(issue_class="other", proposed_severity="S3", affected_services=[], confidence=0.5,
                        evidence_ids=[], rationale="")


@pytest.fixture
def call(sandbox):
    yield llm.LlmCall(agent="triage_agent", incident_id="INC-20260314-001", run_id="RUN-77")
    llm.set_chat_model_factory(None)
    llm.release_budget("RUN-77")


def test_structured_call_uses_function_calling_and_counts_tokens(call):
    llm.set_chat_model_factory(lambda: FakeChatModel(triage_answer))
    result, usage = call.structured(TriageResult, [])
    assert isinstance(result, TriageResult) and usage == {"triage_agent": 1500}
    assert llm.budget("RUN-77").input_tokens == 1200


def test_truncated_output_is_schema_invalid(call):
    llm.set_chat_model_factory(lambda: FakeChatModel(triage_answer, finish_reason="length"))
    with pytest.raises(RecoverableError) as info:
        call.structured(TriageResult, [])
    assert info.value.error_type == ErrorType.SCHEMA_INVALID


def test_provider_errors_are_classified(call):
    class APIConnectionError(Exception):
        pass

    llm.set_chat_model_factory(lambda: FakeChatModel(triage_answer, fail_with=APIConnectionError()))
    with pytest.raises(RecoverableError) as info:
        call.structured(TriageResult, [])
    assert info.value.error_type == ErrorType.LLM_UNAVAILABLE
    llm.set_chat_model_factory(lambda: FakeChatModel(triage_answer, fail_with=ValueError("bad request")))
    with pytest.raises(RecoverableError) as info:
        call.structured(TriageResult, [])
    assert info.value.error_type == ErrorType.LLM_CALL_FAILED


def test_unavailable_llm_and_missing_key(call, sandbox):
    call.llm_available = False
    with pytest.raises(RecoverableError) as info:
        call.structured(TriageResult, [])
    assert info.value.error_type == ErrorType.LLM_UNAVAILABLE
    call.llm_available = True
    with pytest.raises(RecoverableError, match="OPENAI_API_KEY"):  # the sandbox has no key
        call.structured(TriageResult, [])


def test_budget_caps(sandbox):
    sandbox.token_cap_per_run = 1000
    budget = llm.RunBudget("RUN-1")
    budget.add("rca_agent", {"input_tokens": 600, "output_tokens": 100})
    with pytest.raises(CostCapExceeded):
        budget.add("rca_agent", {"input_tokens": 300, "output_tokens": 100})
    sandbox.token_cap_per_run = 10**9
    sandbox.cost_cap_usd_per_run = 0.0001
    with pytest.raises(CostCapExceeded):
        llm.RunBudget("RUN-2").add("rca_agent", {"input_tokens": 1000, "output_tokens": 0})


def test_with_retries_counts_attempts(sandbox):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RecoverableError("try again")
        return "ok"

    assert llm.with_retries(flaky, "x", "rca_agent", "INC-20260314-001", "RUN-1") == "ok" and len(attempts) == 3

    def always():
        raise RecoverableError("never")

    with pytest.raises(RecoverableError) as info:
        llm.with_retries(always, "x", "rca_agent", "INC-20260314-001", "RUN-1")
    assert info.value.attempts == 3  # 1 + MAX_NODE_RETRIES (2)


def test_prompts_render_versions(sandbox, tmp_path, monkeypatch):
    text = prompts.render("rca_agent")
    assert "<untrusted_data>" in text and "Additional guidelines" not in text
    assert prompts.active_version_set() == {a: "v1" for a in prompts.AGENTS}
    versions = tmp_path / "prompt_versions.json"
    versions.write_text('{"agents": {"rca_agent": {"active": "v1", "versions": {"v1": {"guidelines": []}, '
                        '"v2": {"guidelines": ["Check DB client timeouts after releases"], "few_shot": [{"input": "a", '
                        '"output": "b"}]}}}}}', encoding="utf-8")
    monkeypatch.setattr(prompts, "_versions_path", lambda: versions)
    assert prompts.resolve_version("rca_agent", {"rca_agent": "v2"}) == "v2"
    rendered = prompts.render("rca_agent", "v2")
    assert "- Check DB client timeouts after releases" in rendered and "Example 1" in rendered
    with pytest.raises(ValueError):
        prompts.resolve_version("rca_agent", {"rca_agent": "v9"})


def test_no_real_evidence_ids_in_prompts():
    """Example IDs in a prompt are copied by models; the templates must not contain any."""
    import re

    for agent in prompts.AGENTS:
        assert not re.search(r"\b(LOG|KFK|API|DBM|NET|DEP|CMP)-\d{4}\b|RB-[A-Z]{2,4}-\d{3}", prompts.render(agent))


def test_prompt_override_only_in_evaluation_mode():
    with pytest.raises(ValueError):
        initial_state("", prompt_version_override={"rca_agent": "v2"})
    assert initial_state("", run_mode=RunMode.EVALUATION, prompt_version_override={"rca_agent": "v2"})


def test_registry_returns_argument_errors_to_the_model(sandbox):
    from src.tools.implementations import ToolContext
    from src.tools.tool_registry import ToolRegistry

    ctx = ToolContext(scenario_id="SC-01", incident_id="INC-20260314-001", run_id="RUN-1")
    content, flags, result = ToolRegistry().call_for_llm("get_service_dependencies", {"service": "nope"},
                                                         "rca_agent", ctx)
    assert content.startswith("Error: invalid arguments") and result is None
    content, _, _ = ToolRegistry().call_for_llm("search_knowledge_base", {"top_k": 50}, "rca_agent", ctx)
    assert content.startswith("Error: invalid arguments")
