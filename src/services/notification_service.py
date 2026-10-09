"""Notification Service (Architecture Spec Section 5.5; Req. FR-52, FR-69).

No LLM. Looks up stakeholders by exact match, renders templates and sends
through the mock chat and email adapters, after the severity gate.

| Severity group | Chat | Email |
|---|---|---|
| Major | #inc-<incident_id> and the owning team channel | stakeholders for (service, major) |
| Low/Medium | owning team channel only | none |
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src import config
from src.logger_setup import log_interaction
from src.schemas.analysis import SummaryFields
from src.schemas.enums import RunMode, SeverityGroup
from src.schemas.output import Notification, SeverityAssessment
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox
from src.tools.implementations import ToolContext
from src.tools.implementations.get_service_dependencies import load_dependency_map
from src.tools.tool_registry import get_registry

TEMPLATES = Path(__file__).parent / "templates"
NODE = "notify"


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _already_sent(incident_id: str, channel: str, audience: str, level: str) -> bool:
    return any(line.get("incident_id") == incident_id and line.get("channel") == channel
               and line.get("audience") == audience and line.get("severity") == level
               for line in outbox.read("notifications"))


def _storm(service: str, incident_id: str) -> bool:
    """More than STORM_INCIDENT_THRESHOLD incidents on the same root service within 30 minutes."""
    threshold = config.get_settings().storm_incident_threshold
    recent = store.list_incidents(root_service=service, since=datetime.now(timezone.utc) - timedelta(minutes=30))
    return len([v for v in recent if v.incident_id != incident_id]) >= threshold


def notify(incident_id: str, run_id: str, final_severity: SeverityAssessment, summary: SummaryFields,
           needs_human_rca: bool, service: str, run_mode: RunMode = RunMode.LIVE,
           ctx: Optional[ToolContext] = None) -> tuple[list[Notification], dict]:
    """Send the notifications for this severity. Returns (notifications sent, intended when suppressed)."""
    ctx = ctx or ToolContext(incident_id=incident_id, run_id=run_id)
    registry = get_registry()
    level = final_severity.level.value
    major = final_severity.level.group == SeverityGroup.MAJOR
    team = load_dependency_map().get(service, {}).get("owner_team", "production-support")
    extra = []
    if needs_human_rca:
        extra.append("Needs human RCA: the AI's confidence is below the threshold.")
    if "ai_suggests_higher_severity" in final_severity.flags:
        extra.append(f"AI suggests higher severity ({final_severity.llm_proposed_level.value}).")
    values = {"incident_id": incident_id, "level": level, "service": service, "what_happened": summary.what_happened,
              "customer_impact": summary.customer_impact, "current_status": summary.current_status,
              "next_update": summary.next_update, "extra_lines": "\n".join(extra)}

    plan: list[tuple[str, str, str, dict]] = []  # (channel, audience, destination, message)
    if major:
        text = _template("major_chat.txt").format(**values).strip()
        plan.append(("chat", "incident-channel", f"#inc-{incident_id.lower()}", {"text": text}))
        plan.append(("chat", "team-channel", f"#team-{team}", {"text": text}))
        people = registry.call("lookup_stakeholders", {"service": service, "severity_group": "major"}, NODE, ctx)
        subject, _, body = _template("major_email.txt").format(**values).partition("\n")
        plan.append(("email", "stakeholders", ",".join(p["email"] for p in people),
                     {"subject": subject.removeprefix("Subject: ").strip(), "body": body.strip()}))
    else:
        if _storm(service, incident_id):
            log_interaction("dispatch_suppressed", component=NODE, action="notify", reason="storm", service=service,
                            incident_id=incident_id, run_id=run_id)
            return [], {}
        plan.append(("chat", "team-channel", f"#team-{team}",
                     {"text": _template("low_medium_chat.txt").format(**values).strip()}))

    if run_mode == RunMode.EVALUATION:
        intended = {"notify": [{"channel": c, "audience": a, "destination": d} for c, a, d, _ in plan]}
        log_interaction("dispatch_suppressed", component=NODE, action="notify", reason="evaluation_mode",
                        incident_id=incident_id, run_id=run_id)
        return [], intended

    sent: list[Notification] = []
    for channel, audience, destination, message in plan:
        if _already_sent(incident_id, channel, audience, level):
            continue
        if channel == "chat":
            line = registry.call("send_chat_alert", {"channel": destination, "text": message["text"],
                                                     "incident_id": incident_id, "audience": audience,
                                                     "severity": level}, NODE, ctx)
        else:
            line = registry.call("send_email", {"to": destination.split(","), "subject": message["subject"],
                                                "body": message["body"], "incident_id": incident_id,
                                                "audience": audience, "severity": level}, NODE, ctx)
        note = Notification(channel=channel, audience=audience, time=datetime.fromisoformat(line["at"]),
                            outbox_id=line["outbox_id"])
        sent.append(note)
        log_interaction("notification_sent", component=NODE, channel=channel, audience=audience, severity=level,
                        outbox_id=note.outbox_id, incident_id=incident_id, run_id=run_id)
    return sent, {}
