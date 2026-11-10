"""Phone notifications: Web Push (Matan, 31.10).

Five notifications, all to the family rather than one person:
  · A family member joined      : at the moment of joining (`app.join_*`)
  · The salary was recorded     : first salary of the month, when recorded
  · Month closed                : on the 1st of the month, in the morning
  · Last installment            : on the day the last payment of a series is due
  · Nothing recorded for a week : after 7, 14 and 21 days with no new transaction
The last three are sent from `/internal/push/daily`, which `pg_cron` calls once
a day.

Encryption is implemented here (RFC 8291, `aes128gcm`) on top of
`cryptography`, already a dependency, and not through `pywebpush`, which
drags in `http-ece`, a package that doesn't build everywhere. Signing towards
the push service is VAPID (RFC 8292): a `JWT` in ES256.

Without `VAPID_PRIVATE_KEY` in the environment nothing is sent, silently:
locally and in tests.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_RECORD_SIZE = 4096
_TTL_SECONDS = 24 * 3600


# ─── Keys ───────────────────────────────────────────────────────────────────

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.X962,
                                   serialization.PublicFormat.UncompressedPoint)


def generate_vapid_keys() -> tuple:
    """`(private, public)` in base64url, to set on Railway once."""
    key = ec.generate_private_key(ec.SECP256R1())
    d = key.private_numbers().private_value.to_bytes(32, "big")
    return b64u(d), b64u(_point(key.public_key()))


def _vapid_key():
    raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not raw:
        return None
    try:
        return ec.derive_private_key(int.from_bytes(b64u_decode(raw), "big"), ec.SECP256R1())
    except Exception:
        logger.error("VAPID_PRIVATE_KEY is not a valid key; notifications are off")
        return None


_public_cache = {}


def public_key() -> str | None:
    """The public key, for the browser (`applicationServerKey`). `None`: notifications
    are off. Derived once per value of the variable, since it is rendered on every page."""
    raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if raw not in _public_cache:
        key = _vapid_key()
        _public_cache[raw] = b64u(_point(key.public_key())) if key else None
    return _public_cache[raw]


def enabled() -> bool:
    return _vapid_key() is not None


# ─── Encryption (RFC 8291) ──────────────────────────────────────────────────

def _hkdf(salt: bytes, info: bytes, length: int, ikm: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, _server_key=None, _salt=None) -> bytes:
    """The encrypted message body in `aes128gcm` format, one record.

    `_server_key` and `_salt` are for tests only (the RFC's test vectors)."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    if len(ua_public) != 65 or len(auth_secret) < 16:
        raise ValueError("Invalid subscription keys")
    server_key = _server_key or ec.generate_private_key(ec.SECP256R1())
    server_public = _point(server_key.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)

    ikm = _hkdf(auth_secret, b"WebPush: info\x00" + ua_public + server_public, 32, shared)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    if len(payload) + 1 + 16 > _RECORD_SIZE:
        raise ValueError("Message too long")
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + _RECORD_SIZE.to_bytes(4, "big") + bytes([len(server_public)]) + server_public
    return header + body


# ─── VAPID (RFC 8292) ──────────────────────────────────────────────────────

def vapid_header(endpoint: str, key=None, now=None) -> str:
    key = key or _vapid_key()
    url = urlparse(endpoint)
    claims = {
        "aud": f"{url.scheme}://{url.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": os.environ.get("VAPID_SUBJECT", "").strip() or "mailto:matanshneor1@gmail.com",
    }
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    token = f"{head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={b64u(_point(key.public_key()))}"


# ─── Sending ────────────────────────────────────────────────────────────────

def _allowed_endpoint(endpoint: str) -> bool:
    """Real push services only: the URL comes from the browser, and without this
    check the server would send requests to any URL someone registered (SSRF)."""
    try:
        url = urlparse(endpoint)
    except ValueError:
        return False
    host = (url.hostname or "").lower()
    return url.scheme == "https" and (
        host == "fcm.googleapis.com" or host.endswith(".push.apple.com")
        or host.endswith(".notify.windows.com") or host.endswith(".push.services.mozilla.com")
        or host == "updates.push.services.mozilla.com")


def send(subscriptions: list, message: dict) -> list:
    """Sends to every subscriber. Returns the endpoints that no longer exist
    (404/410) so the caller can delete them. A failure for one subscriber
    doesn't stop the rest."""
    key = _vapid_key()
    if not key or not subscriptions:
        return []
    payload = json.dumps(message, ensure_ascii=False).encode()
    dead = []
    with httpx.Client(timeout=10) as client:
        for sub in subscriptions:
            endpoint = sub.get("endpoint") or ""
            if not _allowed_endpoint(endpoint):
                continue
            try:
                body = encrypt(payload, sub.get("p256dh"), sub.get("auth"))
                res = client.post(endpoint, content=body, headers={
                    "Authorization": vapid_header(endpoint, key),
                    "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream",
                    "TTL": str(_TTL_SECONDS),
                    "Urgency": "normal",
                })
                if res.status_code in (404, 410):
                    dead.append(endpoint)
                elif res.status_code >= 400:
                    logger.warning("push: %s from %s", res.status_code, urlparse(endpoint).hostname)
            except Exception:
                logger.warning("push: send failed", exc_info=True)
    return dead


def send_in_background(subscriptions: list, message: dict) -> None:
    """Like `notify.notify_owner`: without delaying the request, and without
    touching the database from the thread."""
    if not subscriptions or not enabled():
        return
    threading.Thread(target=send, args=(list(subscriptions), dict(message)), daemon=True).start()


# ─── Wording ────────────────────────────────────────────────────────────────

from .money import format_money  # noqa: E402
from .wording import HEBREW_MONTHS  # noqa: E402


def _shekels(value) -> str:
    return "₪" + format_money(round(float(value or 0)))


# Wording (Matan, 1.11): no emoji, a short title and one line of details.

def member_joined(name: str, family_name: str = "") -> dict:
    # Matan wrote "נועה הצטרפה". The app has no way to know gender, and a name
    # doesn't tell it; until decided otherwise, the double form
    who = (name or "").strip() or "בן משפחה חדש"
    return {"title": "הצטרפות למשפחה",
            "body": f"{who} הצטרף/ה לתקציב המשפחתי",
            "url": "/settings", "tag": "member-joined"}


def salary(amount, month: int) -> dict:
    return {"title": "משכורת נכנסה",
            "body": f"משכורת של {_shekels(amount)} נרשמה ל{HEBREW_MONTHS[month]}",
            "url": "/", "tag": "salary"}


def month_closed(month: int, income, expense, savings) -> dict:
    return {"title": f"{HEBREW_MONTHS[month]} נסגר",
            "body": f"הכנסות {_shekels(income)} · הוצאות {_shekels(expense)} · חיסכון {_shekels(savings)}",
            "url": "/months", "tag": "month-closed"}


def last_installment(description: str, amount) -> dict:
    # Business name in the title after the Hebrew: at the start of a line an
    # English name flips its direction
    what = (description or "").strip()
    return {"title": f"תשלום אחרון של {what}" if what else "תשלום אחרון",
            "body": f"{_shekels(amount)} פחות בחודש מעכשיו",
            "url": "/settings", "tag": "last-installment"}


def quiet_week(weeks: int) -> dict:
    span = "שבוע" if weeks <= 1 else ("שבועיים" if weeks == 2 else f"{weeks} שבועות")
    return {"title": f"לא נרשמו עסקאות כבר {span}",
            "body": "היכנסו כדי להוסיף עסקאות ולעקוב אחר התקציב שלכם",
            "url": "/", "tag": "quiet-week"}
