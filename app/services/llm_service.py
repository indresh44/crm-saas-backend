from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, Field

from app.config.llm_config import llm_settings
from app.models.chat_metrics import LLMCallMetric

if TYPE_CHECKING:
    from app.services.chat_timer import ChatTimer

logger = logging.getLogger(__name__)

try:
    import litellm
    from litellm import acompletion
except ImportError:  # pragma: no cover - validated at runtime when dependency is installed
    litellm = None
    acompletion = None


class LLMError(Exception):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str
    total_rounds: int = 1
    llm_call_metrics: list[LLMCallMetric] | None = None


class LLMService:
    """Thin wrapper around LiteLLM for provider-agnostic LLM calls."""

    def __init__(self) -> None:
        if litellm is not None:
            litellm.drop_params = True

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        if acompletion is None:
            raise LLMError("LiteLLM is not installed. Install it with `pip install litellm`.")

        request_model = model or llm_settings.primary_model
        request_messages = [{"role": "system", "content": system_prompt}, *messages]
        start = time.perf_counter()

        try:
            response = await acompletion(
                model=request_model,
                messages=request_messages,
                tools=tools,
                api_key=self._resolve_api_key(request_model),
                max_tokens=llm_settings.max_tokens,
                temperature=llm_settings.temperature,
                timeout=llm_settings.timeout,
                drop_params=True,
                reasoning_effort="medium",
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._log_failure(request_model, latency_ms, exc)
            raise self._map_exception(exc) from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        llm_response = self._parse_response(response, fallback_model=request_model)
        logger.info(
            "LLM call completed model=%s input_tokens=%s output_tokens=%s latency_ms=%s stop_reason=%s",
            llm_response.model,
            llm_response.input_tokens,
            llm_response.output_tokens,
            latency_ms,
            llm_response.stop_reason,
        )
        return llm_response

    async def chat_with_tool_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        model: str | None = None,
        max_tool_rounds: int = 5,
        timer: ChatTimer | None = None,
    ) -> LLMResponse:
        working_messages = list(messages)
        total_input_tokens = 0
        total_output_tokens = 0
        rounds = 0
        round_metrics: list[LLMCallMetric] = []

        while True:
            rounds += 1
            llm_start = time.perf_counter()
            response = await self.chat(
                system_prompt=system_prompt,
                messages=working_messages,
                tools=tools,
                model=model,
            )
            llm_duration_ms = (time.perf_counter() - llm_start) * 1000
            if timer:
                timer.record(f"llm_round_{rounds}", "llm", llm_duration_ms)

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            if not response.tool_calls or response.stop_reason != "tool_use":
                round_metrics.append(LLMCallMetric(
                    round_number=rounds,
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    duration_ms=round(llm_duration_ms, 2),
                    triggered_tools=[],
                ))
                response.input_tokens = total_input_tokens
                response.output_tokens = total_output_tokens
                response.total_rounds = rounds
                response.llm_call_metrics = round_metrics
                return response

            if tool_executor is None:
                raise LLMError("Tool calls were returned but no tool_executor was provided.")

            if rounds >= max_tool_rounds:
                raise LLMError(f"Maximum tool rounds reached ({max_tool_rounds}).")

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments),
                        },
                    }
                    for tool_call in response.tool_calls
                ],
            }
            working_messages.append(assistant_message)

            triggered_tools: list[str] = []
            for tool_call in response.tool_calls:
                tool_start = time.perf_counter()
                tool_result = await tool_executor(tool_call.name, tool_call.arguments)
                tool_duration_ms = (time.perf_counter() - tool_start) * 1000
                if timer:
                    timer.record(f"tool:{tool_call.name}", "tool", tool_duration_ms)
                triggered_tools.append(tool_call.name)
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result),
                    }
                )

            round_metrics.append(LLMCallMetric(
                round_number=rounds,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                duration_ms=round(llm_duration_ms, 2),
                triggered_tools=triggered_tools,
            ))

    def _parse_response(self, response: Any, fallback_model: str) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        usage = getattr(response, "usage", None)

        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        for raw_tool_call in raw_tool_calls:
            function = getattr(raw_tool_call, "function", None)
            arguments = getattr(function, "arguments", "{}") if function is not None else "{}"
            tool_calls.append(
                ToolCall(
                    id=str(getattr(raw_tool_call, "id", "")),
                    name=str(getattr(function, "name", "")),
                    arguments=self._parse_json_arguments(arguments),
                )
            )

        finish_reason = getattr(choice, "finish_reason", None) or "stop"
        stop_reason = "tool_use" if tool_calls else str(finish_reason)

        return LLMResponse(
            content=self._normalize_content(getattr(message, "content", None)),
            tool_calls=tool_calls or None,
            stop_reason=stop_reason,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            model=str(getattr(response, "model", fallback_model) or fallback_model),
        )

    def _parse_json_arguments(self, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            return json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Failed to parse tool call arguments as JSON")
            return {}

    def _normalize_content(self, content: Any) -> str | None:
        if content is None or isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(part for part in parts if part) or None
        return str(content)

    def _resolve_api_key(self, model: str) -> str | None:
        model_name = model.lower()
        if model_name.startswith("gemini/"):
            return llm_settings.google_api_key or llm_settings.llm_api_key or os.getenv("GOOGLE_API_KEY")
        if model_name.startswith("anthropic/"):
            return llm_settings.anthropic_api_key or llm_settings.llm_api_key or os.getenv("ANTHROPIC_API_KEY")
        return llm_settings.llm_api_key

    def _map_exception(self, exc: Exception) -> LLMError:
        exc_name = exc.__class__.__name__.lower()
        message = str(exc)
        if "timeout" in exc_name or "timed out" in message.lower():
            return LLMTimeoutError(message)
        if "ratelimit" in exc_name or "rate limit" in message.lower() or "429" in message:
            return LLMRateLimitError(message)
        return LLMError(message)

    def _log_failure(self, model: str, latency_ms: int, exc: Exception) -> None:
        logger.error(
            "LLM call failed model=%s latency_ms=%s error=%s",
            model,
            latency_ms,
            str(exc),
            exc_info=True,
        )
