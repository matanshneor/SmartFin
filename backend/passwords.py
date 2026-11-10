"""What a new password must satisfy (Matan, 10.11.2026, security review).

Two rules, for every new password: signup, reset, and change in settings.
An existing password is never checked: anyone already registered keeps
signing in exactly as before.

  · At least 8 characters (6 until 10.11). Supabase recommends at least 8.
  · Not a leaked password. Lists of hundreds of millions of passwords leaked
    from other sites circulate online, and attackers try them one by one on
    every site. Supabase has this check built in, but only on the paid plan,
    so it lives here.

The check against Pwned Passwords (`api.pwnedpasswords.com`) never sends the
password: only the first five characters of its SHA-1 are sent, and the
service returns every suffix that starts with them (hundreds). The comparison
happens here. `Add-Padding` adds fake rows with a count of 0 to the response,
so that even the response size gives nothing away.

If the service doesn't answer, the password is accepted. A signup stuck on an
external service is worse than one unchecked password.
"""
import hashlib

import httpx

from . import logs

logger = logs.get("smartfin.passwords")

MIN_LENGTH = 8
_RANGE_URL = "https://api.pwnedpasswords.com/range/"
_TIMEOUT = 3.0

TOO_SHORT = f"הסיסמה חייבת להכיל לפחות {MIN_LENGTH} תווים"
LEAKED = ("הסיסמה הזו הופיעה ברשימות של סיסמאות שדלפו מאתרים אחרים, "
          "ולכן קל לנחש אותה — בחרו סיסמה אחרת")


def _fetch_range(prefix: str) -> str:
    r = httpx.get(_RANGE_URL + prefix, timeout=_TIMEOUT,
                  headers={"Add-Padding": "true", "User-Agent": "SmartFin"})
    r.raise_for_status()
    return r.text


def is_leaked(password: str) -> bool:
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = _fetch_range(prefix)
    except httpx.HTTPError as e:
        logger.warning("pwned passwords unavailable (%s) — password accepted unchecked",
                       type(e).__name__)
        return False
    for line in body.splitlines():
        found, _, count = line.strip().partition(":")
        if found == suffix:
            # Padding rows come with a count of 0; they are not real passwords
            return count.strip() not in ("", "0")
    return False


def problem(password: str):
    """The error message for a new password, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return TOO_SHORT
    if is_leaked(password):
        return LEAKED
    return None
