from __future__ import annotations

from pydantic import BaseModel


class ToolCallMetric(BaseModel):
    tool_name: str
    duration_ms: float
    success: bool


class LLMCallMetric(BaseModel):
    round_number: int
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: float
    triggered_tools: list[str] = []


class ChatMessageMetrics(BaseModel):
    total_duration_ms: float
    context_assembly_ms: float
    llm_total_ms: float
    tool_total_ms: float
    suggestion_ms: float = 0.0
    db_total_ms: float = 0.0
    overhead_ms: float = 0.0

    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None = None

    llm_rounds: int
    tools_called: list[str]
    model: str

    llm_calls: list[LLMCallMetric] = []
    tool_calls: list[ToolCallMetric] = []
