"""The access decision: an allowlist, read from the token."""

from service_auth import Principal

from app.access import covers, granted, may_call


def principal(scopes=("tools:call",), details=()):
    return Principal(service="x", scopes=frozenset(scopes), authorization_details=tuple(details), subject="service:x")


def tool_access(*locations, actions=("call",)):
    return {"type": "tool_access", "actions": list(actions), "locations": list(locations)}


def test_a_namespace_covers_its_tools_and_an_exact_location_only_itself():
    assert covers("weather/", "weather/forecast")
    assert not covers("weather/", "weatherman/x")
    assert covers("weather/forecast", "weather/forecast")
    assert not covers("weather/forecast", "weather/current")


def test_no_details_is_no_tools_never_all():
    assert granted(principal()) == ()
    assert not may_call(principal(), "weather/current")


def test_the_scope_is_needed_too():
    assert not may_call(principal(scopes=(), details=[tool_access("weather/")]), "weather/current")


def test_only_tool_access_with_call_counts():
    other = {"type": "file_access", "actions": ["read"], "locations": ["weather/"]}
    assert not may_call(principal(details=[other]), "weather/current")
    assert not may_call(principal(details=[tool_access("weather/", actions=("read",))]), "weather/current")
    assert may_call(principal(details=[tool_access("weather/")]), "weather/current")
