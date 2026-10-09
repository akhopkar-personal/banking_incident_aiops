"""Synchronous bridge to the incident-tools MCP server (Architecture Spec Sections 7.1, 7.5).

One background event loop (a daemon thread) holds one MCP client session for
the life of the process. The session is opened and closed inside a single
task, because the MCP client's anyio scopes must be exited by the task that
entered them. Synchronous callers submit `call_tool` coroutines to that loop
with a timeout, so graph nodes stay synchronous.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
from typing import Any, Optional

from src import config
from src.config import REPO_ROOT
from src.errors import RecoverableError

SERVER_NAME = "incident-tools"
SERVER_MODULE = "src.mcp_server.server"


class McpBridge:
    def __init__(self, command: Optional[list[str]] = None):
        self._command = command or [sys.executable, "-m", SERVER_MODULE]
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._stop: Optional[asyncio.Event] = None
        self._runner: Optional[asyncio.Future] = None
        self.tool_names: list[str] = []

    @property
    def running(self) -> bool:
        return self._session is not None

    def _environment(self) -> dict[str, str]:
        from mcp.client.stdio import get_default_environment

        env = {**get_default_environment(), **config.get_settings().path_environment(), "PYTHONIOENCODING": "utf-8"}
        from src.safety.redaction import PSEUDONYM_KEY_ENV

        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "PYTHONPATH", PSEUDONYM_KEY_ENV):
            if os.environ.get(name):
                env[name] = os.environ[name]
        return env

    async def _hold_session(self, ready: "asyncio.Future[list[str]]") -> None:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({SERVER_NAME: {
            "command": self._command[0], "args": self._command[1:], "transport": "stdio",
            "cwd": str(REPO_ROOT), "env": self._environment(),
        }})
        self._stop = asyncio.Event()
        try:
            async with client.session(SERVER_NAME) as session:
                listed = await session.list_tools()
                self._session = session
                if not ready.done():
                    ready.set_result([tool.name for tool in listed.tools])
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the starter
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._session = None

    def start(self) -> list[str]:
        """Start the server process and open the session; returns the listed tool names."""
        if self.running:
            return self.tool_names
        timeout = config.get_settings().mcp_startup_timeout_s
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-bridge", daemon=True)
        self._thread.start()

        async def launch() -> list[str]:
            ready: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
            self._runner = asyncio.ensure_future(self._hold_session(ready))
            return await ready

        try:
            self.tool_names = asyncio.run_coroutine_threadsafe(launch(), self._loop).result(timeout=timeout)
        except BaseException:
            self.close()
            raise
        return self.tool_names

    def call(self, name: str, arguments: dict[str, Any], timeout: Optional[float] = None) -> Any:
        """Call one tool and return the decoded `result` payload. Failures raise RecoverableError."""
        if not self.running or self._loop is None:
            raise RecoverableError("MCP session is not running")
        timeout = timeout or config.get_settings().tool_call_timeout_s
        future = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, arguments), self._loop)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:  # not the built-in TimeoutError on Python 3.10
            future.cancel()
            raise RecoverableError(f"MCP call {name} timed out after {timeout:.0f} s") from exc
        except Exception as exc:  # noqa: BLE001
            raise RecoverableError(f"MCP call {name} failed: {type(exc).__name__}") from exc
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.isError:
            raise RecoverableError(f"MCP tool {name} returned an error: {text[:300]}")
        payload = result.structuredContent if result.structuredContent is not None else json.loads(text)
        return payload["result"]

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if self._stop is not None:
            loop.call_soon_threadsafe(self._stop.set)
        if self._runner is not None:
            try:
                asyncio.run_coroutine_threadsafe(asyncio.wait_for(asyncio.shield(self._runner), 10), loop).result(15)
            except Exception:  # noqa: BLE001 - best effort shutdown
                pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = self._thread = self._runner = self._stop = None
        self._session = None
