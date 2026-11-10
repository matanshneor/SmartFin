"""
Tests for the filter that scrubs Sentry events before sending.

SmartFin handles financial data, and the receipt-scan route carries an image of a
real receipt. Sending the request body to an external service directly contradicts
the privacy policy, so this promise needs to be enforced by a test and not only
written in a comment: a future upgrade of the SDK's defaults wouldn't be noticed
any other way.

The tests here initially covered only event["request"]: exactly the part that was
scrubbed. They passed in full while the local variables of every frame, including
the password itself, were sent out. A test that confirms only what already works
is worse than no test, because it buys confidence without providing it, so the
new part below tests what leaked.

Completely pure: the filter is a function over a dict.
"""
import pytest

from backend.app import _scrub_event

pytestmark = pytest.mark.unit


def test_request_body_is_removed():
    """The body of /api/receipts/scan contains a receipt image."""
    event = {"request": {"data": {"image": "base64-of-a-real-receipt"}, "url": "/api/receipts/scan"}}

    assert "data" not in _scrub_event(event, {})["request"]


def test_cookies_are_removed():
    """The cookie carries the Supabase tokens: sending it is equivalent to handing over
    the account."""
    event = {"request": {"cookies": {"session": "eyJhbGciOi..."}}}

    assert "cookies" not in _scrub_event(event, {})["request"]


@pytest.mark.parametrize("header", ["Cookie", "Authorization"])
def test_sensitive_headers_are_removed(header):
    event = {"request": {"headers": {header: "secret", "User-Agent": "Safari"}}}

    headers = _scrub_event(event, {})["request"]["headers"]
    assert header not in headers
    assert headers["User-Agent"] == "Safari", "non-sensitive headers stay: they help diagnose"


def test_the_useful_parts_survive():
    """The scrubbing must leave enough for the report to be worth something."""
    event = {
        "request":   {"url": "/month", "method": "GET", "data": {"x": 1}},
        "exception": {"values": [{"type": "KeyError"}]},
    }

    scrubbed = _scrub_event(event, {})
    assert scrubbed["request"]["url"] == "/month"
    assert scrubbed["request"]["method"] == "GET"
    assert scrubbed["exception"]["values"][0]["type"] == "KeyError"


def test_an_event_without_a_request_section_is_untouched():
    """Background errors (for example a script) arrive without a request: this mustn't
    blow up."""
    event = {"exception": {"values": [{"type": "ValueError"}]}}

    assert _scrub_event(event, {}) == event


# ─── What wasn't covered, and leaked ─────────────────────────────────────────

def test_local_variables_never_leave_the_server():
    """The real leak. The SDK attaches to every frame the variables that were in
    memory, so an exception in a sign-in route sent the password itself to an
    external service."""
    event = {"exception": {"values": [{
        "type": "RuntimeError",
        "stacktrace": {"frames": [
            {"function": "login", "vars": {"password": "hunter2",
                                           "identifier": "dana@example.com"}},
            {"function": "inject_auth", "vars": {"refresh_token": "v1.Mr0..."}},
        ]},
    }]}}

    frames = _scrub_event(event, {})["exception"]["values"][0]["stacktrace"]["frames"]

    for frame in frames:
        assert "vars" not in frame, f"local variables were sent from {frame['function']}"


def test_the_stack_itself_survives():
    """Control: without the function names the report is worthless."""
    event = {"exception": {"values": [{"stacktrace": {"frames": [
        {"function": "scan_receipt_route", "lineno": 1420, "vars": {"image_bytes": b"..."}},
    ]}}]}}

    frame = _scrub_event(event, {})["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["function"] == "scan_receipt_route"
    assert frame["lineno"] == 1420


def test_the_invite_code_in_the_query_string_is_removed():
    """/api/family/preview?code=XXXXXX: an invite code is full access to the family."""
    event = {"request": {"url": "/api/family/preview", "query_string": "code=AB12CD"}}

    assert "query_string" not in _scrub_event(event, {})["request"]


@pytest.mark.parametrize("key", ["password", "access_token", "api_key", "image_bytes"])
def test_secrets_sent_explicitly_in_extra_are_masked(key):
    """What the code will send by itself tomorrow, not only what the SDK collects today."""
    event = {"extra": {key: "sensitive", "family_id": "11111111"}}

    scrubbed = _scrub_event(event, {})["extra"]
    assert scrubbed[key] == "[scrubbed]"
    assert scrubbed["family_id"] == "11111111", "identifiers useful for diagnosis stay"


def test_breadcrumb_data_is_masked():
    """The SDK records in breadcrumbs the requests that preceded the error."""
    event = {"breadcrumbs": [
        {"category": "http", "data": {"authorization": "Bearer ey...", "url": "/api/x"}},
    ]}

    data = _scrub_event(event, {})["breadcrumbs"][0]["data"]
    assert data["authorization"] == "[scrubbed]"
    assert data["url"] == "/api/x"


def test_the_sdk_is_configured_not_to_collect_locals_in_the_first_place():
    """Scrubbing is the second layer. The first is not collecting them in the first
    place, and without it every path the filter didn't think of stays open."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/app.py").read_text(encoding="utf-8")

    assert "include_local_variables=False" in src


def test_a_malformed_event_does_not_crash_the_filter():
    """The filter runs on every error. If it blows up itself, we've lost the report."""
    for event in ({}, {"exception": None}, {"breadcrumbs": [None]},
                  {"extra": "not-a-dict"}, {"exception": {"values": [{}]}}):
        _scrub_event(event, {})
