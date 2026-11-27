"""What flows between sandboxes, agent-service and callers.

    events.py      protocol B v0: what callers receive (defined in shared-schemas: shared_schemas.agent_service)
    translate.py   protocol A (from a sandbox: checked, not trusted) → protocol B
Protocol A itself is agent-runtime's `core.types`.
"""
