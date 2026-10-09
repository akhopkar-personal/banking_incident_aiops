"""Prompt-injection detection and untrusted-data delimiters (Architecture Spec
Section 9.1; Req. Section 14, layer 1).

Detection only flags; it never decides anything. The defence that matters is
that every tool result, retrieved chunk and user text reaches the LLM inside
<untrusted_data> tags, and the prompts say such content is data, not
instructions.
"""

from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|your|all|the)\b"
    r"[^.\n]{0,20}\b(instructions?|rules|prompts?|guidelines)\b",
    r"\b(system|developer)\s+(note|prompt|message|instruction)s?\b[^.\n]{0,20}\b(to|for)\s+(the\s+)?"
    r"(ai|assistant|agent|model|llm)\b",
    r"\b(ai|assistant|agent|model|llm)\s*:\s*(disregard|ignore|you must|do not|don't)\b",
    r"#{2,}\s*instruction",
    r"\byou are now\b",
    r"\bact as (an?|the)\b",
    r"\b(call|invoke|run|use)\s+(the\s+)?(page_oncall|itsm_\w+|send_chat_alert|send_email|delete_topic)\b",
    r"\b(set|change|classify)\b[^.\n]{0,40}\bseverity\b[^.\n]{0,15}\b(to|as)\s+S[1-4]\b",
    r"\bdo not (page|escalate|notify)\b",
    r"\bneeds_human_rca\s*=",
)]

OPEN_TAG = "<untrusted_data"
CLOSE_TAG = "</untrusted_data>"


def scan(text: str) -> list[str]:
    """['injection_suspected'] if the text looks like an instruction to the model, else []."""
    if text and any(p.search(text) for p in _PATTERNS):
        return ["injection_suspected"]
    return []


def scan_value(value: object) -> list[str]:
    """Scan every string inside a value (dict, list, str)."""
    if isinstance(value, str):
        return scan(value)
    if isinstance(value, dict):
        items = value.values()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []
    return ["injection_suspected"] if any(scan_value(item) for item in items) else []


def wrap_untrusted(text: str, source: str = "") -> str:
    """Delimit text as data. Tags inside the text are neutralised so it cannot close the block early."""
    safe = re.sub(r"</?\s*untrusted_data[^>]*>", "[tag removed]", text, flags=re.I)
    label = f' source="{re.sub(r"[^A-Za-z0-9_.:-]", "", source)}"' if source else ""
    return f"{OPEN_TAG}{label}>\n{safe}\n{CLOSE_TAG}"
