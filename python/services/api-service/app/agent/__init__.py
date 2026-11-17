"""/v1/agent/…: agent-service for outside machine clients (routes.py has the details)."""

from app.agent.routes import build_router

__all__ = ["build_router"]
