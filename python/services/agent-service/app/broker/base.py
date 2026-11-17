"""What the gateway's model route needs from the broker."""

from __future__ import annotations

from typing import Any, Protocol


class NotSignedIn(RuntimeError):
    """No usable login: sign in once (`python -m app.broker login`), or restore it from OpenBao."""


class ModelLogin(Protocol):
    def headers(self) -> dict[str, str]:
        """The headers that carry the real credential upstream. Raises NotSignedIn."""
        ...

    async def refresh(self) -> None:
        """Refresh the login now (after an upstream 401). One at a time."""
        ...

    def status(self) -> dict[str, Any]:
        """For the status page: signed in, how old the login is, whether OpenBao has it. No secrets."""
        ...
