"""Update emails to the site owner (Matan, 5.10): a new family signed up, someone
joined a family.

Sent through Resend. Without `RESEND_API_KEY` and `OWNER_NOTIFY_EMAIL` in the
environment nothing is sent, silently: locally and in tests there is no key,
and that is the desired state.

Sending runs on a separate thread, so signup doesn't wait for Resend, and so
a failure there (bad key, service down) is never visible to the user. The
thread never touches the database: everything the email needs is fetched in
the request itself and passed in as text. The shared Supabase client is not
safe to use from several threads (see `_run_queries` in app.py).
"""
import html
import logging
import os
import threading

import httpx

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
# Without a verified domain, Resend sends from `onboarding@resend.dev`, and only
# to the email the Resend account was opened with. For mail to the site owner
# that is exactly enough.
_DEFAULT_FROM = "SmartFin <onboarding@resend.dev>"


def notify_owner(subject: str, lines: list) -> bool:
    """Sends to the site owner. Returns whether it was dispatched (not whether it arrived)."""
    key = os.environ.get("RESEND_API_KEY", "").strip()
    to = os.environ.get("OWNER_NOTIFY_EMAIL", "").strip()
    if not key or not to:
        return False
    # Empty line = gap between sections (family details / who it is)
    body_html = "".join(f"<p style=\"margin:0 0 6px\">{html.escape(line)}</p>" if line
                        else "<div style=\"height:10px\"></div>" for line in lines)
    payload = {
        "from": os.environ.get("NOTIFY_FROM", "").strip() or _DEFAULT_FROM,
        "to": [to],
        "subject": subject,
        "text": "\n".join(lines),
        "html": f'<div dir="rtl" style="font-family:Arial,sans-serif;font-size:15px">{body_html}</div>',
    }
    threading.Thread(target=_send, args=(key, payload), daemon=True).start()
    return True


def _send(key: str, payload: dict) -> None:
    try:
        r = httpx.post(_RESEND_URL, json=payload, timeout=10,
                       headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 300:
            logger.warning("notify_owner: Resend %s: %s", r.status_code, r.text[:300])
    except Exception:
        logger.exception("notify_owner")
