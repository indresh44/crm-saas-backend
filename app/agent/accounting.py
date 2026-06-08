"""Token accounting — a separate object the loop appends to after each LLM call.

Reasoning code never touches token logic; the loop wires this in once per call
and the report falls out at the end.

`cached_tokens` (Stage-2 of context-caching work): the count of input tokens
that came from the provider's cached-content store rather than being re-sent
on this call. On Gemini this is populated from `usage.cached_tokens` (or the
nested `usage.prompt_tokens_details.cached_tokens` fallback). Other providers
report 0 when they don't expose a cached count.

The field is additive — every existing caller works unchanged because the
builder's `cached_tokens` arg defaults to 0. The cached portion is INCLUDED
in `input_tokens` (the provider's convention); a cost calculation should
subtract `cached_tokens` from `input_tokens` and apply the discounted rate
to it separately."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnTokens:
    turn: int
    model: str
    input_tokens: int
    output_tokens: int
    # New: cached portion of input_tokens. 0 when the provider doesn't cache
    # or doesn't expose a count. NOT a separate bucket — `cached_tokens` is a
    # subset of `input_tokens`.
    cached_tokens: int = 0


@dataclass(frozen=True)
class TokenReport:
    turns: int
    total_input_tokens: int
    total_output_tokens: int
    per_turn: tuple[TurnTokens, ...]
    total_cached_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "turns": self.turns,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "per_turn": [
                {"turn": t.turn, "model": t.model,
                 "input_tokens": t.input_tokens,
                 "output_tokens": t.output_tokens,
                 "cached_tokens": t.cached_tokens}
                for t in self.per_turn
            ],
        }


class _TokenReportBuilder:
    """Append a TurnTokens after every LLM call. Build() at the end."""

    def __init__(self) -> None:
        self._turns: list[TurnTokens] = []

    def add(
        self,
        turn: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> None:
        self._turns.append(TurnTokens(
            turn=turn, model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
        ))

    def build(self) -> TokenReport:
        return TokenReport(
            turns=len(self._turns),
            total_input_tokens=sum(t.input_tokens for t in self._turns),
            total_output_tokens=sum(t.output_tokens for t in self._turns),
            total_cached_tokens=sum(t.cached_tokens for t in self._turns),
            per_turn=tuple(self._turns),
        )
