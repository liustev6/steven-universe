# tools-service

The agents' tools, on CT 109 (`tools.lan.liustev6.ca`). One tool registry, two interfaces
([tools.md](../../../docs/specs/agent-service/tools.md) TL13):

| Port | Interface | For |
|---|---|---|
| 443 | REST API: `GET /v1/tools`, `GET /v1/weather/current?place=…`, `GET /v1/weather/forecast?place=…&days=…` | apps: Hey Pi's dashboard (pi-server), the phone (through api-service) |
| 8443 | MCP, Streamable HTTP, at `/mcp` | agents: agent-service's gateway; MCP clients such as Claude Code |

Every request carries an auth-service token for `tools-service` with `tools:call`. What a caller may
use is its grant's `tool_access` locations (`weather/`, `pi/`), the same on both interfaces: the
list shows only those tools, and anything else is refused (403 on the API, "Unknown tool" on MCP)
and audited. No details in a token means no tools.

| Tool | Location | MCP name |
|---|---|---|
| the weather now (Open-Meteo) | `weather/current` | `weather_current` |
| the daily forecast, 1–7 days | `weather/forecast` | `weather_forecast` |
| end Hey Pi's voice conversation (pi-server carries it out) | `pi/end_conversation` | `pi_end_conversation` |

**Code:** `app/registry.py` (each tool once, and the one call path: check, arguments, run, audit),
`app/access.py` (the decision), `app/api.py` and `app/mcp_server.py` (the two interfaces),
`app/weather/` (Open-Meteo, the dashboard's format), `app/main.py` (both listeners).

**Develop:**

    uv venv -p 3.11 .venv && uv pip install -p .venv -r requirements-dev.txt
    .venv/bin/python -m pytest -q

**Try it with Claude Code** (a token for a client granted tools at tools-service, 10 minutes):

    claude mcp add --transport http tools https://tools.lan.liustev6.ca:8443/mcp --header "Authorization: Bearer <token>"

**Deploy:** `infra/deployment/deploy.sh tools-service` (manifest: `infra/services/tools-service/`).
