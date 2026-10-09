"""Exceptions shared across modules (Architecture Spec Sections 4.4, 4.5, 7.1)."""

from __future__ import annotations

from src.schemas.enums import ErrorType


class RecoverableError(Exception):
    """A failure the node wrapper retries (timeout, rate limit, malformed file,
    schema-invalid LLM output). After the retries it becomes a SYSTEM_ERROR."""

    def __init__(self, message: str, error_type: ErrorType = ErrorType.TOOL_CALL_FAILED):
        super().__init__(message)
        self.error_type = error_type


class ToolAccessDenied(Exception):
    """A caller asked for a tool the access policy does not allow (Req. FR-66)."""

    def __init__(self, tool: str, caller: str, reason: str):
        super().__init__(f"{caller!r} may not call {tool!r}: {reason}")
        self.tool = tool
        self.caller = caller
        self.reason = reason


class CostCapExceeded(Exception):
    """The per-run token or cost cap was exceeded (Req. 13.2). Not retried."""
