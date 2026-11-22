"""LLM provider abstraction.

Agents never import a vendor SDK. They call `LLMClient.structured(...)` and get
back a validated Pydantic object plus token usage. Swapping Groq for Gemini or
OpenAI is a one-line change in `.env`.

Reliability lives here too:
* every call has a timeout
* retries are bounded, with backoff
* the last attempt switches strategy (plain JSON instead of tool-calling),
  because some models fail structured output on larger schemas
* if everything fails, `LLMError` is raised and the *agent* decides the
  fallback (see `agents/base.py`)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.config.settings import Settings, get_settings
from backend.orchestrator.state import Usage

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model could not produce a valid answer within the retry budget."""


def build_chat_model(settings: Settings) -> Any:
    """Create the LangChain chat model for the configured provider."""
    provider = settings.llm_provider
    api_key = settings.provider_api_key()
    if provider == "offline":
        return None
    if not api_key:
        raise LLMError(
            f"LLM_PROVIDER={provider} but its API key is empty. "
            "Set the key in .env or use LLM_PROVIDER=offline."
        )
    common = {
        "model": settings.resolved_model,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        # Retries are handled in LLMClient so they are counted and bounded once.
        "max_retries": 0,
    }
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(api_key=api_key, **common)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(google_api_key=api_key, **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(api_key=api_key, **common)
    raise LLMError(f"Unknown LLM provider: {provider}")


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (handles ``` fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(candidate[start : end + 1])


def _usage_from(message: Any) -> Usage:
    meta = getattr(message, "usage_metadata", None) or {}
    return Usage(
        input_tokens=int(meta.get("input_tokens", 0) or 0),
        output_tokens=int(meta.get("output_tokens", 0) or 0),
        llm_calls=1,
    )


class LLMClient:
    def __init__(self, settings: Settings | None = None, model: Any = None):
        self.settings = settings or get_settings()
        self._model = model
        self._model_built = model is not None

    @property
    def is_offline(self) -> bool:
        return self.settings.llm_provider == "offline" and self._model is None

    @property
    def mode(self) -> str:
        if self.is_offline:
            return "offline"
        return f"{self.settings.llm_provider}:{self.settings.resolved_model}"

    def _get_model(self) -> Any:
        if not self._model_built:
            self._model = build_chat_model(self.settings)
            self._model_built = True
        return self._model

    def _callbacks(self) -> list[Any]:
        from observability.tracing import langfuse_callbacks

        return langfuse_callbacks(self.settings)

    def _run_config(self) -> dict[str, Any]:
        from observability.tracing import current_langfuse_context, langfuse_config

        ctx = current_langfuse_context()
        config = langfuse_config(
            self.settings,
            session_id=ctx.get("session_id"),
            user_id=ctx.get("user_id"),
            trace_name=ctx.get("trace_name"),
            tags=ctx.get("tags"),
            metadata=ctx.get("metadata"),
        )
        return config or {"callbacks": self._callbacks()}

    async def _invoke_tool_calling(
        self, messages: list[tuple[str, str]], schema: type[T]
    ) -> tuple[T, Usage]:
        runnable = self._get_model().with_structured_output(schema, include_raw=True)
        out = await runnable.ainvoke(messages, config=self._run_config())
        usage = _usage_from(out.get("raw"))
        parsed = out.get("parsed")
        if parsed is None:
            raise ValueError(f"structured output not parsed: {out.get('parsing_error')}")
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed)
        return parsed, usage

    async def _invoke_json_text(
        self, messages: list[tuple[str, str]], schema: type[T]
    ) -> tuple[T, Usage]:
        instruction = (
            "Reply with a single JSON object and nothing else. It must conform to "
            f"this JSON Schema:\n{json.dumps(schema.model_json_schema())}"
        )
        reply = await self._get_model().ainvoke(
            [*messages, ("user", instruction)],
            config=self._run_config(),
        )
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        return schema.model_validate(extract_json(content)), _usage_from(reply)

    async def structured(
        self, system: str, user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        """Return a validated `schema` instance, or raise `LLMError`."""
        if self.is_offline:
            raise LLMError("LLM provider is offline")

        messages = [("system", system), ("user", user)]
        attempts = 1 + max(0, self.settings.llm_max_retries)
        total = Usage()
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            use_json_text = attempt == attempts and attempts > 1
            call = self._invoke_json_text if use_json_text else self._invoke_tool_calling
            try:
                parsed, usage = await asyncio.wait_for(
                    call(messages, schema), timeout=self.settings.llm_timeout_seconds
                )
                total.add(usage)
                return parsed, total
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"LLM call exceeded {self.settings.llm_timeout_seconds}s"
                )
            except (ValidationError, ValueError) as exc:
                last_error = exc  # malformed output: worth another attempt
            except Exception as exc:  # provider/network error
                last_error = exc
            total.llm_calls += 1
            logger.warning(
                "LLM attempt %d/%d failed: %s", attempt, attempts, last_error
            )
            if attempt < attempts:
                await asyncio.sleep(self.settings.retry_backoff_seconds * attempt)

        raise LLMError(f"LLM failed after {attempts} attempts: {last_error}")
