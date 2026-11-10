"""
Tests for identifying the client's address behind Railway's proxy.

Found while verifying rate limiting in production: ProxyFix was configured with
x_proto and x_host only, without x_for, so remote_addr was Railway's edge node
and not the user. 12 requests from one computer were counted under four
addresses and the limit wasn't enforced.

The more dangerous side is the opposite: all users come through the same handful
of edge addresses, so one noisy user could exhaust the quota and lock sign-in for
everyone.

The value x_for=2 was measured against production, not guessed. Railway adds two
layers, and X-Forwarded-For arrives as "<client>, <edge node>". ProxyFix counts
from the right, so x_for=1 returned the edge node. The tests here pin this
topology: if Railway changes it, they will fail, and that is exactly the point.
"""
import pytest

from backend.app import app

pytestmark = pytest.mark.unit

EDGE       = "152.233.12.241"   # a Railway edge node, as actually observed
OTHER_EDGE = "152.233.13.166"

# The probe is registered once only: Flask blocks registering routes after the
# first request.
_captured = {}


@app.route("/__ip_probe")
def _ip_probe():
    from flask import request
    _captured["addr"] = request.remote_addr
    return "ok"


@pytest.fixture
def seen_ip():
    """Runs a request with a given proxy chain and returns what Flask saw as
    remote_addr, i.e. exactly the key get_remote_address will use."""
    app.config["TESTING"] = True

    def run(forwarded_for):
        _captured.clear()
        with app.test_client() as c:
            c.get("/__ip_probe",
                  headers={"X-Forwarded-For": forwarded_for},
                  environ_overrides={"REMOTE_ADDR": "100.64.0.2"})
        return _captured.get("addr")

    return run


def test_the_real_client_ip_is_used(seen_ip):
    assert seen_ip(f"203.0.113.7, {EDGE}") == "203.0.113.7"


def test_two_users_behind_one_edge_are_told_apart(seen_ip):
    """The core of the bug: two users through the same node must get separate keys;
    otherwise one exhausts the other's quota."""
    first  = seen_ip(f"203.0.113.7, {EDGE}")
    second = seen_ip(f"198.51.100.23, {EDGE}")

    assert first != second
    assert {first, second} == {"203.0.113.7", "198.51.100.23"}


def test_one_user_across_two_edges_stays_one_key(seen_ip):
    """The other side: the same user through two edge nodes is counted once;
    otherwise the limit is multiplied by the number of nodes, and that is what
    actually happened."""
    assert seen_ip(f"203.0.113.7, {EDGE}") == seen_ip(f"203.0.113.7, {OTHER_EDGE}")


def test_a_spoofed_entry_does_not_win(seen_ip):
    """Counting from the right is what prevents spoofing: a value the client pushes
    itself is pushed left and never chosen. (In practice Railway also strips such
    headers before they arrive, which was measured, but we don't rely on that
    alone.)"""
    assert seen_ip(f"1.2.3.4, 203.0.113.7, {EDGE}") == "203.0.113.7"
