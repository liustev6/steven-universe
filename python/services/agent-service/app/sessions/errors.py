"""The sessions core's errors: each one maps to one HTTP answer in the sessions API."""

from __future__ import annotations


class SessionError(Exception):
    """Base class; `str()` is safe to show the caller."""


class NotFound(SessionError):          # 404: doesn't exist, or isn't yours (never say which, C2)
    pass


class UnknownProfile(SessionError):    # 400
    pass


class InstructionsNotAllowed(SessionError):   # 400: the profile takes no caller instructions (C15)
    pass


class ProfileMismatch(SessionError):   # 409: a conversation keeps its profile (C6)
    pass


class Busy(SessionError):              # 409: a turn is already running
    pass


class Ended(SessionError):             # 409: the session is over; start a new one in the conversation
    pass


class BudgetExceeded(SessionError):    # 429: the profile's max_turns (C13)
    pass


class Stopped(SessionError):           # 503: the kill switch is on (C14)
    pass
