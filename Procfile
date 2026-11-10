# --workers 4: a receipt scan blocks a worker for the whole OpenAI call, which takes
# 2-6 seconds. With two, two parallel scans take the site down for every other
# user: not slow it, take it down. Four give breathing room, at a smaller memory
# cost than it seems thanks to --preload (see below).
#
# ⚠️ Do not add --threads, and do not switch to gevent or any other async worker:
# not as an optimisation and not as an experiment. The Supabase client is a
# process-level singleton (_client in supabase_config), and set_auth_token changes
# its authorization header globally on every request. With a sync worker this is
# safe, because it handles one request at a time. With threads, one user's request
# would run with another user's token: one family would see another's money.
#
# This isn't theory: this project already got hit by it once, in a milder form,
# when parallel calls on the same client returned "Server disconnected" and showed
# ₪0. The safe way to scale is more processes, not more threads inside a process.
# For threads to become safe, the client needs to be per request (flask.g) and not
# module-level.
#
# --preload: the app is loaded once before the fork, and the copies share its
# memory. Without it every worker loads everything again (~60MB each).
# Deliberate side effect: a load error fails the deploy instead of crashing each
# worker separately in an endless loop.
web: gunicorn backend.app:app --bind 0.0.0.0:$PORT --workers 4 --preload --timeout 120
