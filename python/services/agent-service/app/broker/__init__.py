"""The auth broker (model-access.md MA1–MA6, control-plane.md C11): your ChatGPT login, held here
and nowhere else. The gateway's model route asks it for the real credential.

This file has the interface the gateway uses; the broker itself (login, refresh, OpenBao) is
piece f.
"""

from __future__ import annotations

from typing import Protocol


class NotSignedIn(RuntimeError):
    """No usable login: run the login once (device code), or restore it from OpenBao."""


class ModelLogin(Protocol):
    def headers(self) -> dict[str, str]:
        """The headers that carry the real credential upstream. Raises NotSignedIn."""
        ...

    async def refresh(self) -> None:
        """Refresh the login now (after an upstream 401). One at a time."""
        ...
