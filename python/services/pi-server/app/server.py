"""
pi-server's core: the state it holds, how it answers each message from
hey-pi, and the mock data loops.
"""

import asyncio
import itertools
import logging
import time
from typing import Optional

from pydantic import ValidationError

from app.core.config import Settings
from app.hub import Client, Hub
from app.mock.assistant import reply_for
from app.mock.home_assistant import ActionError, Entity, MockHomeAssistant
from app.mock.weather import MockWeather
from app.protocol import (
    Assistant,
    AssistantCancel,
    AssistantPhase,
    AssistantPtt,
    AssistantSnapshot,
    AssistantText,
    Connection,
    DisplayState,
    HaAction,
    HaActionResult,
    HaState,
    HaStateBody,
    Hello,
    PROTOCOL_VERSION,
    SettingsMessage,
    SettingsUpdate,
    Snapshot,
    Subscribe,
    UserActivity,
    WidgetEntry,
    WidgetUpdate,
    client_message,
)
from app.settings_store import SettingsStore

logger = logging.getLogger(__name__)


def now_ms() -> int:
    return int(time.time() * 1000)


class PiServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.hub = Hub()
        self.store = SettingsStore(settings.DATA_DIR / "settings.json")
        self.device_settings = self.store.load()
        self.lab_online = True

        self.weather = MockWeather()
        self.weather_ts = now_ms()
        self.ha = MockHomeAssistant()
        self.ha_ts = {entity_id: now_ms() for entity_id in self.ha.entities}

        self.assistant_phase: AssistantPhase = "idle"
        self.turn_id: Optional[str] = None
        self._turn_ids = itertools.count(1)
        self._turn_task: Optional[asyncio.Task] = None
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self.settings.MOCK:
            self._tasks.append(asyncio.create_task(self._weather_loop()))

    async def stop(self) -> None:
        for task in [*self._tasks, self._turn_task]:
            if task:
                task.cancel()

    async def _weather_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.MOCK_WEATHER_INTERVAL)
            self.weather.next()
            self.weather_ts = now_ms()
            await self.hub.broadcast(
                WidgetUpdate(widget="weather", data=self.weather.data, ts=self.weather_ts),
                feed="weather",
            )

    # --- connections --------------------------------------------------------

    async def on_connect(self, client: Client) -> None:
        """Device state every client gets right away, before its hello."""
        await self.hub.send(client, self._connection())
        await self.hub.send(client, SettingsMessage(**self.device_settings.model_dump()))

    async def handle(self, client: Client, raw: str) -> None:
        """One message from hey-pi. Unknown or malformed ones are logged and
        dropped; they never close the connection."""
        try:
            message = client_message.validate_json(raw)
        except ValidationError as error:
            logger.warning("Dropping message from %s: %s", client.name, error.errors()[:1])
            return

        match message:
            case Hello():
                client.name = message.client
                if message.protocol_version != PROTOCOL_VERSION:
                    logger.warning(
                        "%s speaks protocol %s, pi-server speaks %s",
                        client.name, message.protocol_version, PROTOCOL_VERSION,
                    )
            case Subscribe():
                client.feeds = set(message.feeds)
                await self.hub.send(client, self.snapshot())
            case UserActivity():
                pass  # Later: tell the backlight/idle logic the user is there.
            case HaAction():
                await self._ha_action(client, message)
            case AssistantText():
                self.start_turn(message.text)
            case AssistantPtt(action="start"):
                await self._set_phase(self._new_turn(), "listening")
            case AssistantPtt(action="stop"):
                self.start_turn("(push-to-talk)", turn_id=self.turn_id)
            case AssistantCancel():
                await self._cancel_turn()
            case SettingsUpdate():
                await self._update_settings(message)

    # --- snapshot -----------------------------------------------------------

    def snapshot(self) -> Snapshot:
        return Snapshot(
            assistant=AssistantSnapshot(phase=self.assistant_phase, turn_id=self.turn_id),
            widgets={"weather": WidgetEntry(data=self.weather.data, ts=self.weather_ts)},
            ha_states=[
                self._ha_body(entity_id, entity)
                for entity_id, entity in self.ha.entities.items()
            ],
        )

    def _ha_body(self, entity_id: str, entity: Entity) -> HaStateBody:
        return HaStateBody(
            entity_id=entity_id,
            state=entity.state,
            attributes=entity.attributes,
            ts=self.ha_ts[entity_id],
        )

    # --- Home Assistant -----------------------------------------------------

    async def _ha_action(self, client: Client, action: HaAction) -> None:
        try:
            entity = self.ha.call(action.entity_id, action.service)
        except ActionError as error:
            await self.hub.send(client, HaActionResult(id=action.id, ok=False, error=str(error)))
            return
        self.ha_ts[action.entity_id] = now_ms()
        await self.hub.send(client, HaActionResult(id=action.id, ok=True))
        body = self._ha_body(action.entity_id, entity)
        await self.hub.broadcast(HaState(**body.model_dump()), feed=f"ha:{action.entity_id}")

    # --- assistant ----------------------------------------------------------

    def _new_turn(self) -> str:
        return f"t{next(self._turn_ids)}"

    def start_turn(self, text: str, turn_id: Optional[str] = None) -> None:
        if self._turn_task:
            self._turn_task.cancel()
        self._turn_task = asyncio.create_task(self._run_turn(turn_id or self._new_turn(), text))

    async def _run_turn(self, turn_id: str, text: str) -> None:
        await self._set_phase(turn_id, "thinking", transcript=text)
        await asyncio.sleep(self.settings.MOCK_REPLY_WORD_DELAY * 4)
        await self._set_phase(turn_id, "speaking")
        for word in reply_for(text).split(" "):
            await self.hub.broadcast(
                Assistant(turn_id=turn_id, phase="speaking", reply_delta=word + " ")
            )
            await asyncio.sleep(self.settings.MOCK_REPLY_WORD_DELAY)
        await self._set_phase(turn_id, "idle")

    async def _cancel_turn(self) -> None:
        if self._turn_task:
            self._turn_task.cancel()
            self._turn_task = None
        await self._set_phase(self.turn_id, "idle")

    async def _set_phase(
        self, turn_id: Optional[str], phase: AssistantPhase, transcript: Optional[str] = None
    ) -> None:
        self.turn_id = turn_id
        self.assistant_phase = phase
        await self.hub.broadcast(
            Assistant(turn_id=turn_id, phase=phase, transcript=transcript)
        )

    # --- settings -----------------------------------------------------------

    async def _update_settings(self, update: SettingsUpdate) -> None:
        changes = update.model_dump(exclude={"type"}, exclude_none=True)
        self.device_settings = self.device_settings.model_copy(update=changes)
        self.store.save(self.device_settings)
        # Every screen follows the change, including the one that sent it.
        await self.hub.broadcast(SettingsMessage(**self.device_settings.model_dump()))

    # --- device state pushed from outside (debug endpoints, later hardware) --

    async def set_display(self, state: str) -> None:
        await self.hub.broadcast(DisplayState(state=state))

    async def set_lab_online(self, online: bool) -> None:
        self.lab_online = online
        await self.hub.broadcast(self._connection())

    def _connection(self) -> Connection:
        return Connection(lab="online" if self.lab_online else "offline")
