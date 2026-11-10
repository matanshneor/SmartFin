"""Deleting an image that was scanned and not saved, and only it.

The scan uploads the image before there is a transaction. An abandoned scan left an
orphan file, and the nightly cleanup that was supposed to collect it failed on
every run. So the browser asks to delete at the moment of abandoning. The danger:
a way to delete the receipt of an existing transaction, or of another family.
"""
import pytest

from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
# names as upload_receipt creates them (see _receipt_path_ok)
_ABC  = f"{_FAM}/5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b.jpg"
_KEPT = f"{_FAM}/6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d.jpg"


@pytest.fixture
def discard(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    deleted = []
    in_use = set()
    monkeypatch.setattr(db, "delete_receipt", lambda token, path: deleted.append(path))
    monkeypatch.setattr(db, "receipt_in_use", lambda path, fid: path in in_use)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield (lambda path: c.post("/api/receipts/discard", json={"path": path})), deleted, in_use


def test_an_abandoned_scan_is_deleted(discard):
    post, deleted, _ = discard

    assert post(_ABC).status_code == 200
    assert deleted == [_ABC]


def test_a_receipt_that_a_transaction_uses_is_not(discard):
    """Otherwise this is a way to delete the receipt of an existing transaction, even
    on a save that already succeeded."""
    post, deleted, in_use = discard
    in_use.add(_KEPT)

    assert post(_KEPT).status_code == 409
    assert deleted == []


@pytest.mark.parametrize("path", [
    "99999999-9999-9999-9999-999999999999/x.jpg",     # another family
    f"{_FAM}/../99999999-9999-9999-9999-999999999999/x.jpg",
    "x.jpg", "", None, 5,
    f"{_FAM}/abc.jpg",                                # a name the app doesn't create
])
def test_only_this_familys_folder(discard, path):
    post, deleted, _ = discard

    assert post(path).status_code == 404
    assert deleted == []


def test_a_failed_check_deletes_nothing(discard, monkeypatch):
    """"Don't know if in use" isn't "not in use"."""
    post, deleted, _ = discard
    def broken(*a):
        raise db.DataUnavailable("down")
    monkeypatch.setattr(db, "receipt_in_use", broken)

    assert post(_ABC).status_code == 503
    assert deleted == []
