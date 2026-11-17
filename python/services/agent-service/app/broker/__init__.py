"""The auth broker (model-access.md, control-plane.md C11): your ChatGPT login, held here and
nowhere else.

    base.py      the interface the gateway's model route uses (ModelLogin, NotSignedIn)
    broker.py    AuthBroker: credentials per request, refresh, restore and save, status
    codex.py     the Codex SDK calls: device-code sign-in, refresh
    store.py     the durable copy in OpenBao (role agent-service)
    __main__.py  `python -m app.broker login | status`, on CT 107
"""

from app.broker.base import ModelLogin, NotSignedIn
from app.broker.broker import AuthBroker
from app.broker.store import LoginStore, OpenBaoStore, StoreError

__all__ = ["AuthBroker", "LoginStore", "ModelLogin", "NotSignedIn", "OpenBaoStore", "StoreError"]
