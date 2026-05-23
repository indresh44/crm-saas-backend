"""Write surface — the assistant's safe path for committing writes.

This package is the new foundation for layer 2; it deliberately does NOT extend
the existing chat_orchestrator/tool_executor confirm flow. Currently it contains
only the prepared-action store; the prepare/commit engine and capability
declarations are later steps.
"""
