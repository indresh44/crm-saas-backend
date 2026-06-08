"""Layer 6 — the reasoning loop.

Thin ReAct-style agent that drives the read model (layer 1) and write surface
(layer 2). One action per turn, hard step budget of 8, deterministic observation
summarization, separate token accounting, sequential prepare/confirm.

Entry points: `run_agent` (the loop) and `confirm_prepared_action` (the post-
human-confirm commit; NOT part of the loop).
"""
