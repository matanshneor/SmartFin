"""Application logging.

The code used to use print only: 61 calls, most of them inside except. That
output goes to Railway's log buffer, which gets wiped, and nobody reads it.
More importantly, **Sentry never saw it**. We hooked up error monitoring and
then routed the app's most common failure mode around it: a query that fails
and is swallowed.

With logging, Sentry's logging integration automatically turns every record
at ERROR and above into an event, and lower records into breadcrumbs attached
to the next event. Same line of code, but now it is visible.

Use logger.exception (not logger.error) inside except: it attaches the full
traceback, which is exactly what is missing to know why the query failed.
"""
import logging
import os
import sys


def setup():
    """Points logging at stderr, where Railway collects it.

    Called once from app.py. `force=True` because gunicorn has already touched
    the logging config before the app is loaded, and without it the setup here
    is silently ignored."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    # Too noisy at INFO, and they add nothing to what we already log
    for noisy in ("httpx", "hpack", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
