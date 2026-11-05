"""
Health check, for systemd and debugging: curl localhost:8765/health
"""

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(request: Request):
    server = request.app.state.pi
    return {
        "status": "healthy",
        "version": server.settings.APP_VERSION,
        "clients": len(server.hub.clients),
        "lab": "online" if server.lab_online else "offline",
        "mock": server.settings.MOCK,
    }
