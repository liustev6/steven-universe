"""
Mock Home Assistant entities until home-hub relays the real ones.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entity:
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class ActionError(Exception):
    pass


class MockHomeAssistant:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {
            "light.living_room": Entity("on", {"friendly_name": "Living room"}),
            "light.kitchen": Entity("off", {"friendly_name": "Kitchen"}),
            "light.bedroom": Entity("off", {"friendly_name": "Bedroom"}),
            "scene.movie": Entity("scening", {"friendly_name": "Movie night"}),
        }

    def call(self, entity_id: str, service: str) -> Entity:
        """Run a service like "light.toggle" on an entity; returns its new state."""
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ActionError(f"unknown entity {entity_id}")
        domain = entity_id.split(".", 1)[0]
        if service == f"{domain}.toggle":
            entity.state = "off" if entity.state == "on" else "on"
        elif service == f"{domain}.turn_on" and domain != "scene":
            entity.state = "on"
        elif service == f"{domain}.turn_off":
            entity.state = "off"
        elif service != "scene.turn_on":
            raise ActionError(f"unsupported service {service} for {entity_id}")
        return entity
