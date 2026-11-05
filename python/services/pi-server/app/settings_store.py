"""
Device settings saved on the Pi (e.g. light/dark mode).

One small JSON file, written atomically (temp file, then rename), so a power
cut never leaves it half-written. A missing or corrupt file means defaults.
"""

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from app.protocol import DeviceSettings

logger = logging.getLogger(__name__)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> DeviceSettings:
        try:
            return DeviceSettings.model_validate_json(self.path.read_text())
        except FileNotFoundError:
            return DeviceSettings()
        except (ValidationError, ValueError) as error:
            logger.warning("Ignoring unreadable %s: %s", self.path, error)
            return DeviceSettings()

    def save(self, settings: DeviceSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.model_dump(mode="json"), indent=2))
        os.replace(tmp, self.path)
