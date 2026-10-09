"""DeepEval judge that uses function calling (Architecture Spec Section 11.1).

DeepEval's own OpenAI model asks for strict structured output, which made
gpt-4o-mini emit thousands of whitespace tokens in the Phase 0 smoke test. This
judge uses the project's ChatOpenAI settings with function calling and an
output cap instead.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from deepeval.models import DeepEvalBaseLLM

from src import config


def _default_chat_model() -> Any:
    from langchain_openai import ChatOpenAI

    s = config.get_settings()
    return ChatOpenAI(model=s.judge_model, base_url=s.openai_base_url, api_key=s.require_openai_key(),
                      temperature=0, timeout=max(s.llm_timeout_s, 60), max_retries=2,
                      max_tokens=s.llm_max_output_tokens)


class FunctionCallingJudge(DeepEvalBaseLLM):
    """`chat_model` replaces ChatOpenAI (tests pass a scripted model)."""

    def __init__(self, chat_model: Optional[Any] = None) -> None:
        self._chat_model = chat_model
        super().__init__(model=config.get_settings().judge_model)

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        return self._chat_model if self._chat_model is not None else _default_chat_model()

    def generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        if schema is None:
            return self.model.invoke(prompt).content
        return self.model.with_structured_output(schema, method="function_calling").invoke(prompt)

    async def a_generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return f"{config.get_settings().judge_model} (function calling)"
