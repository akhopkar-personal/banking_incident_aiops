"""Recommendation Agent (Architecture Spec Section 6.5; Req. FR-15, FR-16, FR-18, FR-48).

With no hypothesis, or a top confidence below INSUFFICIENT_THRESHOLD, there is
no LLM call: the result explains what evidence is missing. Otherwise one
hypothesis-targeted document search per top-2 hypothesis, then one structured
call. Correlated changes are linked to the hypothesis that cites them.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src import config as app_config
from src.agent.agent_support import AgentRun, fmt_documents, fmt_incident, fmt_rules
from src.safety import action_policy
from src.schemas.analysis import ChangeCorrelationResult, RecommendationResult, RetrievedDocument, SummaryFields
from src.services.gates import latest_rules

AGENT = "recommendation_agent"
DOC_TYPES = ["runbook", "postmortem", "verified_resolution", "regulatory"]

# What to collect when a source is missing, for the insufficient-evidence result.
SOURCE_NAMES = {"LOG": "application logs", "KFK": "Kafka events", "API": "API metrics", "DBM": "database metrics",
                "NET": "network events", "DEP": "change records", "CMP": "customer complaints",
                "CON": "Kafka Connect status", "ACL": "ACL audit", "SRG": "Schema Registry", "QRM": "cluster quorum"}


def insufficient(state: dict[str, Any], reason: str, top: float) -> RecommendationResult:
    incident = state["incident"]
    missing = [SOURCE_NAMES.get(s, s) for s in incident.withheld_sources]
    collect = ("Collect " + ", ".join(missing) + " for the incident window, then re-run the investigation."
               if missing else "Collect logs and metrics for the affected services and re-run the investigation.")
    return RecommendationResult(
        root_cause_summary="No root cause could be established from the available evidence.", top_confidence=top,
        recommended_actions=[],
        summary_fields=SummaryFields(
            what_happened=f"An incident affecting {incident.reported_service or 'banking services'} is under "
                          "investigation.",
            customer_impact="Customer impact is being assessed.",
            current_status="The incident is with the response team for manual investigation.",
            next_update="Within 60 minutes."),
        regulatory_notes="", insufficient_evidence_reason=f"{reason} {collect}", cited_doc_ids=[],
        cited_evidence_ids=[])


def link_changes(changes: Optional[ChangeCorrelationResult], rca) -> Optional[ChangeCorrelationResult]:
    """A finding is linked to the highest-ranked hypothesis that cites its change ID."""
    if changes is None or rca is None:
        return changes
    findings = []
    for finding in changes.findings:
        rank = next((h.rank for h in rca.hypotheses if finding.change_id in h.supporting_evidence), None)
        findings.append(finding.model_copy(update={"linked_hypothesis_rank": rank}))
    return changes.model_copy(update={"findings": findings})


def run(state: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    a = AgentRun(AGENT, state, config)
    rca, changes = state.get("rca"), state.get("changes")
    hypotheses = rca.hypotheses if rca else []
    threshold = app_config.get_settings().insufficient_threshold
    linked = link_changes(changes, rca)
    if not hypotheses:
        return {"recommendation": insufficient(state, "No root-cause hypothesis is supported by the evidence.", 0.0),
                "changes": linked, **a.common_update()}
    top = hypotheses[0].confidence
    if top < threshold:
        return {"recommendation": insufficient(
            state, f"The best hypothesis has confidence {top:.2f}, below {threshold:.2f}.", top),
            "changes": linked, **a.common_update()}

    rules = latest_rules(state)
    docs: list[RetrievedDocument] = []
    for hypothesis in hypotheses[:2]:
        docs += a.tool("search_knowledge_base", {"query": hypothesis.root_cause, "top_k": 4, "doc_types": DOC_TYPES})
    if rules and "regulatory_flag" in rules.flags:
        docs += a.tool("search_knowledge_base", {"query": "incident reporting obligations data integrity duplicate "
                                                          "debits customer remediation", "top_k": 2,
                                                 "doc_types": ["regulatory"]})
    known_docs = {(d.doc_id, d.section): d for d in [*(state.get("retrieved_documents") or []), *docs]}
    policy = action_policy.load_policy()
    action_types = "\n".join(f"- {name}: {rule.risk_level.value} risk"
                             f"{', needs a runbook citation' if rule.requires_runbook else ''}"
                             f"{', never first' if not rule.can_be_first_step else ''}"
                             for name, rule in policy.action_types.items())
    evidence = state.get("evidence") or {}
    cited = sorted({e for h in hypotheses for e in h.supporting_evidence} | {f.change_id for f in
                                                                              (linked.findings if linked else [])})
    evidence_lines = "\n".join(f"[{e}] {evidence[e].summary}" for e in cited if e in evidence)
    context = "\n\n".join([
        fmt_incident(a.incident),
        "Severity (rules): " + fmt_rules(rules),
        "Hypotheses:\n" + "\n".join(
            f"{h.rank}. ({h.confidence:.2f}) {h.root_cause} | supporting: {', '.join(h.supporting_evidence)} | "
            f"against or missing: {'; '.join(h.contradicting_or_missing_evidence) or 'none'}" for h in hypotheses),
        "Correlated changes:\n" + ("\n".join(
            f"{f.change_id} ({f.change_type.value} on {f.service}, {f.time_before_onset_min:g} min before onset, "
            f"linked to hypothesis {f.linked_hypothesis_rank}): {f.rationale}" for f in linked.findings)
            if linked and linked.findings else "(none)"),
        "Cited evidence:\n" + a.untrusted(evidence_lines or "(none)", "evidence"),
        "Documents (cite as 'DOC-ID §n' using the section number):\n"
        + a.untrusted(fmt_documents(known_docs.values()), "search_knowledge_base"),
        "Allowed action types:\n" + action_types,
    ])
    result = a.structured(RecommendationResult, [SystemMessage(a.system_prompt()), HumanMessage(context)])
    result = result.model_copy(update={"top_confidence": top, "insufficient_evidence_reason": ""})
    # The RCA Agent cannot see change records, so a change the recommendation cites as part of the root
    # cause is linked to the top hypothesis (change-correlation hit rate, Req. 12.2).
    if linked is not None:
        cited = set(result.cited_evidence_ids)
        linked = linked.model_copy(update={"findings": [
            f.model_copy(update={"linked_hypothesis_rank": 1}) if f.linked_hypothesis_rank is None
            and f.change_id in cited else f for f in linked.findings]})
    return {"recommendation": result, "retrieved_documents": docs, "changes": linked, **a.common_update()}
