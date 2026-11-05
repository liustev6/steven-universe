"""
The hey-pi <-> pi-server protocol: JSON over WebSocket, one object per
message, tagged by "type". See docs/specs/flutter_ui.md ("Protocol").

- Client protocol: what home-hub will speak; pi-server relays it (mocked for
  now).
- Device protocol: pi-server only (display state, lab connection, settings).

Models here mirror hey-pi's Dart models. Keep both in sync.
"""

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1

ThemeMode = Literal["dark", "light"]
AssistantPhase = Literal["idle", "listening", "thinking", "speaking"]


# --- hey-pi -> pi-server ----------------------------------------------------


class Hello(BaseModel):
    type: Literal["hello"]
    protocol_version: int
    client: str = "unknown"


class Subscribe(BaseModel):
    type: Literal["subscribe"]
    # e.g. ["weather", "ha:light.living_room"]
    feeds: list[str]


class UserActivity(BaseModel):
    type: Literal["user_activity"]


class HaAction(BaseModel):
    type: Literal["ha_action"]
    id: str
    entity_id: str
    service: str
    data: dict[str, Any] = Field(default_factory=dict)


class AssistantText(BaseModel):
    type: Literal["assistant_text"]
    text: str


class AssistantPtt(BaseModel):
    type: Literal["assistant_ptt"]
    action: Literal["start", "stop"]


class AssistantCancel(BaseModel):
    type: Literal["assistant_cancel"]


class SettingsUpdate(BaseModel):
    """Device protocol: change saved device settings."""

    type: Literal["settings_update"]
    theme_mode: Optional[ThemeMode] = None


ClientMessage = Annotated[
    Union[
        Hello,
        Subscribe,
        UserActivity,
        HaAction,
        AssistantText,
        AssistantPtt,
        AssistantCancel,
        SettingsUpdate,
    ],
    Field(discriminator="type"),
]

client_message = TypeAdapter(ClientMessage)


# --- pi-server -> hey-pi ----------------------------------------------------


class WidgetEntry(BaseModel):
    data: dict[str, Any]
    ts: int


class HaStateBody(BaseModel):
    entity_id: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    ts: int


class AssistantSnapshot(BaseModel):
    phase: AssistantPhase = "idle"
    turn_id: Optional[str] = None


class Snapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    protocol_version: int = PROTOCOL_VERSION
    assistant: AssistantSnapshot
    widgets: dict[str, WidgetEntry]
    ha_states: list[HaStateBody]


class WidgetUpdate(BaseModel):
    type: Literal["widget_update"] = "widget_update"
    widget: str
    data: dict[str, Any]
    ts: int


class HaState(HaStateBody):
    type: Literal["ha_state"] = "ha_state"


class HaActionResult(BaseModel):
    type: Literal["ha_action_result"] = "ha_action_result"
    id: str
    ok: bool
    error: Optional[str] = None


class Assistant(BaseModel):
    type: Literal["assistant"] = "assistant"
    turn_id: Optional[str]
    phase: AssistantPhase
    transcript: Optional[str] = None
    reply_delta: Optional[str] = None


class DisplayState(BaseModel):
    """Device protocol: force the display state (e.g. wake word -> active)."""

    type: Literal["display_state"] = "display_state"
    state: Literal["active", "sleep"]


class Connection(BaseModel):
    """Device protocol: whether pi-server's link to home-hub is up."""

    type: Literal["connection"] = "connection"
    lab: Literal["online", "offline"]


class DeviceSettings(BaseModel):
    """Device settings saved by pi-server."""

    theme_mode: ThemeMode = "dark"


class SettingsMessage(DeviceSettings):
    """Device protocol: the saved settings (on connect and after a change)."""

    type: Literal["settings"] = "settings"


def encode(message: BaseModel) -> dict[str, Any]:
    """A message as a JSON-ready dict (unset optional fields omitted)."""
    return message.model_dump(mode="json", exclude_none=True)
