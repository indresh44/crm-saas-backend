"""Read-model layer.

A deliberately narrow, declarative query surface that an AI assistant may use
to read business data safely. The package currently contains only the schema
whitelist (``schema.py``). The request model, query compiler, and virtual-field
resolvers are intentionally NOT built yet.

See:
- ``Docs/read-model-audit.md``        — the audit this layer is built on
- ``Docs/canonical-definitions-v1.md`` — canonical derived-value definitions
"""
