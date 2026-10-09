"""Output checks (Architecture Spec Section 9.4; Req. Section 14, layer 3).

Each check returns the (possibly corrected) value and a list of guardrail flags.
None of them raises on bad content: they remove, replace or flag it.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from src.schemas.analysis import Hypothesis, RcaResult, RecommendationResult, RetrievedDocument
from src.schemas.enums import ErrorType
from src.schemas.output import ErrorDetail
from src.safety.redaction import redact_record

M = TypeVar("M", bound=BaseModel)

# ------------------------------------------------------------------ schema


def validate_schema(model: type[M], data: Any) -> tuple[Optional[M], list[str]]:
    """Parse `data` into `model`; (None, ['schema_invalid']) if it does not validate."""
    if isinstance(data, model):
        return data, []
    try:
        return model.model_validate(data), []
    except ValidationError:
        return None, ["schema_invalid"]


# --------------------------------------------------------------- grounding

_DOC_ID = re.compile(r"\b(RB-[A-Z]{3}-\d{3}|PM-\d{4}-\d{3}|REG-\d{3}|SVC-\d{3}|VR-INC-\d{8}-\d{3})\b")


def cited_doc_id(citation: str) -> Optional[str]:
    """'RB-PAY-003 §2' -> 'RB-PAY-003'."""
    match = _DOC_ID.search(citation or "")
    return match.group(1) if match else None


def check_grounding(recommendation: RecommendationResult, rca: Optional[RcaResult], evidence_ids: Iterable[str],
                    retrieved: list[RetrievedDocument]
                    ) -> tuple[RecommendationResult, Optional[RcaResult], list[str], bool]:
    """Remove claims that cite unknown evidence and actions that cite documents not retrieved
    (or unverified ones). Returns (recommendation, rca, flags, insufficient)."""
    known = set(evidence_ids)
    docs = {d.doc_id: d for d in retrieved}
    flags: list[str] = []

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    rca_out = rca
    if rca is not None:
        hypotheses: list[Hypothesis] = []
        for h in rca.hypotheses:
            supported = [e for e in h.supporting_evidence if e in known]
            if len(supported) != len(h.supporting_evidence):
                flag("ungrounded_removed")
            if supported:
                hypotheses.append(h.model_copy(update={"supporting_evidence": supported, "rank": len(hypotheses) + 1}))
        rca_out = rca.model_copy(update={"hypotheses": hypotheses})

    actions = []
    for action in recommendation.recommended_actions:
        doc_id = cited_doc_id(action.runbook_citation)
        if action.runbook_citation.strip():
            doc = docs.get(doc_id) if doc_id else None
            if doc is None or doc.citation_status == "unverified":
                flag("ungrounded_removed")
                continue
            if doc.stale:
                flag("stale_citation")
        actions.append(action)
    cited_evidence = [e for e in recommendation.cited_evidence_ids if e in known]
    cited_docs = [d for d in recommendation.cited_doc_ids if d in docs and docs[d].citation_status == "verified"]
    if len(cited_evidence) != len(recommendation.cited_evidence_ids) or len(cited_docs) != len(
            recommendation.cited_doc_ids):
        flag("ungrounded_removed")
    if any(docs[d].stale for d in cited_docs):
        flag("stale_citation")
    out = recommendation.model_copy(update={
        "recommended_actions": [a.model_copy(update={"step": i}) for i, a in enumerate(actions, start=1)],
        "cited_evidence_ids": cited_evidence, "cited_doc_ids": cited_docs,
    })
    no_hypothesis = rca_out is not None and not rca_out.hypotheses
    insufficient = no_hypothesis or not out.recommended_actions
    return out, rca_out, flags, insufficient


# --------------------------------------------------------------------- tone

DISALLOWED_WORDS = [
    "fault", "blame", "blamed", "negligent", "negligence", "incompetent", "careless", "stupid", "idiot",
    "guaranteed", "definitely", "obviously", "screwed", "messed up", "catastrophe", "disaster",
]
# A person or team as the subject of a failure verb. Systems ("the release caused") are fine.
_BLAME = re.compile(
    r"\b(engineers?|developers?|team|vendor|partner|supplier|operators?|staff|someone|employees?|admins?)\b"
    r"[^.]{0,30}\b(caused|broke|failed to|forgot|mistake|error by|was responsible)\b", re.I)
_IMPACT_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%|percent|customers|users|accounts|transactions|complaints)",
                            re.I)
NEUTRAL_SENTENCE = "The cause is being investigated and updates will follow."
NEUTRAL_IMPACT = "Customer impact is being assessed."


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_tone(summary: str, allowed_numbers: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Replace sentences that blame, use disallowed words, or state an impact number that is
    not in `allowed_numbers` (numbers backed by evidence, as strings, for example '212')."""
    allowed = {n.replace(",", "") for n in allowed_numbers}
    out, changed = [], False
    for sentence in _sentences(summary):
        lower = sentence.lower()
        if _BLAME.search(sentence) or any(re.search(rf"\b{re.escape(w)}\b", lower) for w in DISALLOWED_WORDS):
            out.append(NEUTRAL_SENTENCE)
            changed = True
            continue
        numbers = [m.group(1).replace(",", "") for m in _IMPACT_NUMBER.finditer(sentence)]
        if any(n not in allowed for n in numbers):
            out.append(NEUTRAL_IMPACT)
            changed = True
            continue
        out.append(sentence)
    # Collapse repeated neutral sentences.
    deduped = [s for i, s in enumerate(out) if i == 0 or s != out[i - 1]]
    return " ".join(deduped), (["tone_adjusted"] if changed else [])


# ---------------------------------------------------------------------- PII


def recheck_pii(value: Any) -> tuple[Any, list[str]]:
    """Run redaction again over all free text; flag pii_redacted_output if anything changed."""
    redacted, found = redact_record(value)
    return redacted, (["pii_redacted_output"] if found else [])


# ------------------------------------------------------------ error message

STANDARD_ERROR_MESSAGES: dict[ErrorType, str] = {
    ErrorType.TOOL_CALL_FAILED: "A data source could not be read, so the AI investigation could not complete. "
                                "Monitoring rules still decided severity and dispatch.",
    ErrorType.LLM_CALL_FAILED: "The AI model returned an error, so the investigation could not complete. "
                               "Monitoring rules still decided severity and dispatch.",
    ErrorType.LLM_UNAVAILABLE: "The on-call team was paged based on monitoring rules, but the AI investigation could "
                               "not complete. No recommendation was generated.",
    ErrorType.KILL_SWITCH: "AI investigation is switched off (rules-only mode). Monitoring rules decided severity "
                           "and dispatch. No recommendation was generated.",
    ErrorType.COST_CAP_EXCEEDED: "The investigation stopped at its cost limit. Monitoring rules still decided "
                                 "severity and dispatch.",
    ErrorType.SCHEMA_INVALID: "The AI output could not be validated, so no recommendation is shown. Monitoring "
                              "rules still decided severity and dispatch.",
    ErrorType.UNHANDLED_EXCEPTION: "The investigation could not complete because of an internal error. Monitoring "
                                   "rules still decided severity and dispatch.",
}
_LEAKY = re.compile(r"Traceback|File \"|line \d+|\b\w+(Error|Exception)\b|0x[0-9a-f]{6,}|[A-Za-z]:\\|/usr/|/home/")


def check_error_message(detail: ErrorDetail) -> tuple[ErrorDetail, list[str]]:
    """Replace a message that leaks a stack trace, exception class, path or unredacted text."""
    redacted, found = redact_record(detail.message)
    if _LEAKY.search(detail.message) or found or not detail.message.strip():
        return detail.model_copy(update={"message": STANDARD_ERROR_MESSAGES[detail.error_type]}), [
            "error_message_replaced"]
    return detail, []


def is_stale(last_verified: Optional[date], stale_days: int, today: Optional[date] = None) -> bool:
    return last_verified is not None and ((today or date.today()) - last_verified).days > stale_days
