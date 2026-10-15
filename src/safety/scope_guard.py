"""Refuse requests outside the agent's remit (Req. FR-77; Scenario 2 safety requirements).

The system investigates incidents and recommends steps for humans. It never
moves money, approves anything, gives legal advice, changes data or runs
operational actions. A request for any of these is refused at intake, before
an incident is created or any LLM sees the text, with a message that says who
can help instead. The check is deterministic: a fixed set of patterns, so the
same text always gets the same answer.

A request is an imperative or a direct ask ("reverse the debits", "can you
approve the refund", "please restart payments-service"). A description of
the incident ("fund transfers keep failing", "customers charged twice") is not
a request and passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Where a request starts: the beginning of a sentence or a direct ask.
_START = r"(?:^|[.!?;:\n]\s*|,\s*)"
_ASK = (r"(?:please|pls|kindly|can you|could you|would you|will you|i want you to|i need you to|i'd like you to|"
        r"we need to|need you to|go ahead and|help me|let's|lets|just|now|immediately)")
_ADVICE_ASK = r"(?:should we|should i|shall we|shall i|do we have to|can we|can i)"
_DETERMINER = (r"(?:the|this|that|these|those|his|her|their|my|our|its|all|a|an|both|each|every|customer|customers|"
               r"account|accounts)")
_AMOUNT = r"(?:₹|rs\.?|inr|usd|\$|£|€)\s?\d|\d[\d,.]*\s?(?:rupees|dollars|inr|usd|lakh|crore)"
_MONEY_NOUN = r"(?:money|funds?|amount|amounts|payment|payments|transfer|transfers|refund|refunds|charge|charges|" \
              r"debit|debits|credit|transaction|transactions|balance)"


@dataclass(frozen=True)
class Refusal:
    category: str  # money_movement, approval, legal_advice, data_change_or_action
    message: str

    @property
    def reason(self) -> str:
        return f"refused_{self.category}"


MESSAGES = {
    "money_movement": "I can't move money: no transfers, reversals, refunds or credits. I only investigate incidents "
                      "and recommend steps. A reversal or refund must go through the payments operations team's "
                      "approved process; if an incident is behind the request, describe what is failing and since "
                      "when, and I will investigate it.",
    "approval": "I can't approve or authorise anything (payments, refunds, limits, exceptions or changes). Approvals "
                "stay with the accountable person under the bank's delegation rules; ask the incident commander or "
                "your manager. I can investigate the incident and give evidence for their decision.",
    "legal_advice": "I can't give legal advice or judge liability. Please contact the bank's legal or compliance "
                    "team. I can investigate the incident and point to the internal regulatory notes that apply.",
    "data_change_or_action": "I can't change data or run operational actions. I only read telemetry and recommend "
                             "steps; an engineer carries them out after review. Describe what is failing and since "
                             "when, and I will investigate and recommend the next steps.",
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("legal_advice", re.compile(
        r"\b(?:legal advice|legally|liab(?:le|ility)|lawsuit|sue|sued|suing|litigation|lawyer|attorney|"
        r"legal action|legal position|legal risk)\b", re.I)),
    ("money_movement", re.compile(
        rf"(?:{_START}|\b{_ASK}\s+|\b{_ADVICE_ASK}\s+)(?:reverse|refund|reimburse|credit|debit|transfer|wire|"
        rf"chargeback|charge back|waive)\s+(?:{_DETERMINER}\b|{_MONEY_NOUN}\b|{_AMOUNT})", re.I)),
    ("money_movement", re.compile(
        rf"(?:{_START}|\b{_ASK}\s+|\b{_ADVICE_ASK}\s+)(?:send|move|pay|pay out|release|return|initiate|process|"
        rf"make|execute|top up)\s+(?:\w+\s+){{0,2}}(?:{_MONEY_NOUN}\b|{_AMOUNT})", re.I)),
    ("approval", re.compile(
        rf"(?:{_START}|\b{_ASK}\s+|\b{_ADVICE_ASK}\s+)(?:approve|authori[sz]e|sanction|sign off(?: on)?|"
        rf"green-?light|grant)\s+(?:\w+\s+){{0,2}}(?:refund|refunds|payment|payments|transfer|transfers|loan|"
        rf"limit|limits|request|transaction|transactions|reversal|exception|waiver|change|changes|release|"
        rf"rollback|deployment|it|this|that)\b", re.I)),
    ("data_change_or_action", re.compile(
        rf"(?:{_START}|\b{_ASK}\s+)(?:delete|purge|drop|truncate|modify|update|change|edit|overwrite)\s+"
        rf"(?:\w+\s+){{0,2}}(?:balance|balances|record|records|account|accounts|data|table|tables|database|ledger|"
        rf"transaction|transactions|limit|limits|customer|customers|entries|entry|topic|topics)\b", re.I)),
    ("data_change_or_action", re.compile(
        rf"(?:{_START}|\b{_ASK}\s+)(?:restart|reboot|roll ?back|deploy|fail ?over|scale|kill|disable|"
        rf"freeze|unfreeze|block|unblock|close|reset|renew|revoke)\s+(?:{_DETERMINER}\b|[a-z]+-(?:service|gateway|"
        rf"app|db|platform)\b|kafka|broker|brokers|cluster|connector|connectors|certificate|cert|route|sessions?)",
        re.I)),
]


def check_request(text: str) -> Optional[Refusal]:
    """A Refusal if the text asks for something outside the agent's remit; None otherwise."""
    if not text:
        return None
    for category, pattern in _PATTERNS:
        if pattern.search(text):
            return Refusal(category, MESSAGES[category])
    return None
