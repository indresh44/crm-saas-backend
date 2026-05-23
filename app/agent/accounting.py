"""Token accounting — a separate object the loop appends to after each LLM call.

Reasoning code never touches token logic; the loop wires this in once per call
and the report falls out at the end."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnTokens:
    turn: int
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class TokenReport:
    turns: int
    total_input_tokens: int
    total_output_tokens: int
    per_turn: tuple[TurnTokens, ...]

    def to_dict(self) -> dict:
        return {
            "turns": self.turns,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "per_turn": [
                {"turn": t.turn, "model": t.model,
                 "input_tokens": t.input_tokens, "output_tokens": t.output_tokens}
                for t in self.per_turn
            ],
        }


class _TokenReportBuilder:
    """Append a TurnTokens after every LLM call. Build() at the end."""

    def __init__(self) -> None:
        self._turns: list[TurnTokens] = []

    def add(self, turn: int, model: str, input_tokens: int, output_tokens: int) -> None:
        self._turns.append(TurnTokens(turn=turn, model=model,
                                      input_tokens=input_tokens, output_tokens=output_tokens))

    def build(self) -> TokenReport:
        return TokenReport(
            turns=len(self._turns),
            total_input_tokens=sum(t.input_tokens for t in self._turns),
            total_output_tokens=sum(t.output_tokens for t in self._turns),
            per_turn=tuple(self._turns),
        )
