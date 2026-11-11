# Screenshot harness

Renders every screen of the real app (templates, CSS, JS, and the Python that
shapes the data) over an in-memory demo family, and photographs it. Nothing
touches Supabase or the network: `serve.py` swaps `db.get_client()` for the
`FakeSupabase` double in `tests/_fake_db.py`, refuses `httpx`, pins the clock,
and registers its own sign-in route (`/__h/login`). `app.py` is not changed.

```
pip install -r requirements.txt pillow       # in any venv
PY=.venv/bin/python SHOTS=before tests/visual/run.sh          # all groups
PY=.venv/bin/python SHOTS=after ONLY='month' tests/visual/run.sh B D
python tests/visual/diff.py before after     # exit 1 on any changed pixel
```

Groups: **A** every page (light, 390×844), **B** sheets, dialogs, toasts and the
install bar, **C** home variants and empty states, **D** dark, **E** 320×568 with
extra-large text, **S** long names and large amounts. Output goes to
`tests/visual/out/<label>/` (git-ignored), with `index.json` describing each shot.

Two runs of the same code are pixel-identical; if they are not, fix that first,
or every comparison after it is noise.

Limits: Chromium only (no safe areas, no iOS keyboard or date picker), and
emoji come from whatever colour-emoji font the machine has. Check those on a
phone.
