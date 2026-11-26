"""Smoke test against the running tools-service, both interfaces, with the real service_auth library.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the just-built
image (it has service_auth, httpx and the MCP SDK) with host networking, so requests go through DNS,
ports 443 and 8443 and TLS exactly like a real caller's.

It authenticates as `smoke-test`, whose only grant at tools-service is `weather/forecast` (auth-service
policy.yaml): it must see that tool and only that tool, on the REST API and on MCP, and be refused the
others. Open-Meteo being down is reported, not failed: a deploy isn't rolled back for a third party.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from service_auth import TokenClient

ISSUER = "https://auth.lan.liustev6.ca"
API = "https://tools.lan.liustev6.ca"
MCP = "https://tools.lan.liustev6.ca:8443/mcp"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def weather_ok(status: int, name: str, detail: str) -> None:
    if status == 502:
        print(f"WARN  {name}: Open-Meteo unavailable, not a failure of this deploy  ({detail})")
    else:
        check(name, status == 200, detail)


async def main() -> int:
    tokens = TokenClient(ISSUER, "smoke-test", Path("/t/secret").read_text().strip())
    token = await tokens.token("tools-service")
    auth = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=API, timeout=20) as api:
        r = await api.get("/health")
        check("API: health", r.status_code == 200 and r.json().get("tools", 0) > 0, r.text[:120])
        r = await api.get("/v1/tools")
        check("API: no token is refused", r.status_code == 401, str(r.status_code))
        r = await api.get("/v1/tools", headers=auth)
        names = [t["name"] for t in r.json().get("tools", [])] if r.status_code == 200 else []
        check("API: lists only its grant", names == ["weather/forecast"], str(names))
        r = await api.get("/v1/weather/forecast", params={"place": "Seattle, US", "days": 2}, headers=auth)
        weather_ok(r.status_code, "API: a forecast", f"{r.status_code} {r.text[:100]}")
        r = await api.get("/v1/weather/current", params={"place": "Seattle"}, headers=auth)
        check("API: a tool not granted is refused", r.status_code == 403, str(r.status_code))

    async with httpx2.AsyncClient(timeout=20) as raw:
        r = await raw.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check("MCP: no token is refused", r.status_code == 401, str(r.status_code))

    async with Client(streamable_http_client(MCP, http_client=httpx2.AsyncClient(headers=auth, timeout=20))) as mcp:
        names = [t.name for t in (await mcp.list_tools()).tools]
        check("MCP: lists only its grant", names == ["weather_forecast"], str(names))
        result = await mcp.call_tool("weather_forecast", {"place": "Seattle, US", "days": 1})
        if result.is_error and "weather service" in result.content[0].text:
            weather_ok(502, "MCP: a forecast", result.content[0].text)
        else:
            check("MCP: a forecast", not result.is_error and bool(result.structured_content.get("days")),
                  result.content[0].text[:100])
        try:
            await mcp.call_tool("weather_current", {"place": "Seattle"})
            check("MCP: a tool not granted is refused", False, "it ran")
        except MCPError as e:
            check("MCP: a tool not granted is refused", "Unknown tool" in str(e), str(e))

    await tokens.aclose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
