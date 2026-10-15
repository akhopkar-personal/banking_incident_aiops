"""Forced demonstration of tool selection and its safeguards (Req. FR-76). No LLM, no cost.

    python scripts/demo_tool_safeguards.py

Sends the calls a model could make through the same code the agents use
(`ToolRegistry.call_for_llm` and the RCA agent's `plan_calls`) against the
SC-04 telemetry, and prints what happened to each:

  1. a correct call (the right tool for the RCA Agent, valid arguments): runs
  2. wrong arguments (a service name that does not exist): rejected, the error goes back to the model
  3. a tool outside the agent's allowed set (change records belong to the Change Correlation Agent): blocked
  4. a dispatch tool asked for by an agent (paging): blocked; only workflow nodes may dispatch
  5. a repeated call and calls beyond the tool budget: skipped (loop prevention)

Every outcome is logged with this demo's run ID; the script ends by printing its flow log.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.agent import rca_agent
    from src.config import get_settings
    from src.logger_setup import configure_logging
    from src.services import flow_log
    from src.tools.implementations._common import ToolContext, parse_time
    from src.tools.tool_registry import get_registry

    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    settings.apply_to_environment()
    configure_logging()
    manifest = json.loads((settings.scenario_dir("SC-04") / "manifest.json").read_text(encoding="utf-8"))
    stamp = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    ctx = ToolContext(scenario_id="SC-04", incident_id=f"INC-DEMO-TOOLS-{stamp}", run_id=f"RUN-DEMO-TOOLS-{stamp}",
                      window_start=parse_time(manifest["window_start"]), window_end=parse_time(manifest["window_end"]))
    registry = get_registry()
    agent = rca_agent.AGENT

    def show(step: str, caller: str, tool: str, args: dict) -> None:
        content, flags, result = registry.call_for_llm(tool, args, caller, ctx)
        outcome = "ran" if result is not None else ("blocked" if flags else "rejected")
        print(f"\n{step}\n  {caller} -> {tool}({json.dumps(args)})\n  outcome: {outcome}"
              + (f", flags {flags}" if flags else "") + f"\n  the model receives: {content[:220]}"
              + ("…" if len(content) > 220 else ""))

    try:
        show("1. Correct tool and arguments", agent, "query_network", {"service": ["payment-network-gateway"]})
        show("2. Wrong arguments (unknown service name)", agent, "query_api_metrics", {"service": ["payments"]})
        show("3. Tool outside the agent's allowed set", agent, "query_change_records", {})
        show("4. Dispatch tool asked for by an agent", "recommendation_agent", "page_oncall",
             {"incident_id": ctx.incident_id, "severity": "S1", "summary": "test"})

        budget = settings.rca_tool_budget
        calls = [{"id": f"c{i}", "name": "query_logs", "args": {"service": ["payment-network-gateway"]}}
                 for i in range(2)]  # the same call twice
        calls += [{"id": f"d{i}", "name": "query_api_metrics", "args": {"service": [s]}} for i, s in enumerate(
            ["payment-network-gateway", "payments-service", "api-gateway", "mobile-app", "net-banking",
             "auth-service", "accounts-service", "notification-service", "core-banking-db"])]
        allowed, skipped = rca_agent.plan_calls(calls, used=0, budget=budget, seen=set())
        rca_agent.log_skipped(skipped, ctx.incident_id, ctx.run_id)
        print(f"\n5. Loop prevention: the model asks for {len(calls)} calls with a budget of {budget}")
        print(f"  run: {len(allowed)}; skipped: " + ", ".join(f"{c['name']} {c['args']['service'][0]} ({status})"
                                                          for c, status in skipped))
    finally:
        registry.close()

    entries = flow_log.load(ctx.run_id)
    print(f"\nFlow log of {ctx.run_id} (also: python scripts/show_flow_log.py {ctx.run_id})\n")
    print(flow_log.to_text(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
