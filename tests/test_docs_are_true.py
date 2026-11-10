"""The docs describe this app, not a previous one.

The README claimed 46 routes when there were 56, and 29 migrations when there were
41. `SPEC.md` described an app with no projects, no receipt scanning, no budgets
and no manager role, i.e. about half of what exists. The repo is public, and this
is the first document anyone reads.

The docs rotted because nothing checked them. The tests here don't check that
they are well written; they check that the numbers in them are right, and that
they hold no real account details. When a route or migration is added, one of
them will fail and say what to update.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_SPEC = (_ROOT / "docs/SPEC.md").read_text(encoding="utf-8")


def _claimed(pattern, text=_README):
    """The number the docs claim. Fails loudly if the wording changed: a test that
    doesn't find what it checks always passes, and that is worse than not
    checking."""
    match = re.search(pattern, text)
    assert match, f"the claim wasn't found in the docs: {pattern}"
    return int(match.group(1))


# ─── The numbers ────────────────────────────────────────────────────────────

def test_the_readme_counts_the_routes_correctly():
    actual = len(re.findall(r"^@app\.route", (_ROOT / "backend/app.py")
                            .read_text(encoding="utf-8"), re.M))
    claimed = _claimed(r"all (\d+) routes")

    assert claimed == actual, (
        f"the README says {claimed} routes, and there are actually {actual}. "
        f"Update README.md."
    )


def test_the_readme_counts_the_migrations_correctly():
    actual = len(list((_ROOT / "backend/supabase/migrations").glob("*.sql")))
    claimed = _claimed(r"database schema \((\d+) migrations\)")

    assert claimed == actual, (
        f"the README says {claimed} migrations, and there are actually {actual}. "
        f"Update README.md."
    )


def test_the_readme_counts_the_tests_roughly_right():
    """An approximation is enough here: the tests grow all the time, and an exact
    number would turn this test into a chore. What is caught is a number that
    went stale.

    The previous floor (5 times the number of files) was too loose: the README
    said 976 when there were 1,765, and it passed. Now: at least the number of
    test functions themselves; `parametrize` only adds to that, so it is always
    true when the number is current."""
    functions = sum(len(re.findall(r"^\s*def test_", f.read_text(encoding="utf-8"), re.M))
                    for f in (_ROOT / "tests").glob("test_*.py"))
    claimed = _claimed(r"(\d+) tests: \d+ unit")

    assert claimed >= functions, (
        f"the README says {claimed} tests, and there are {functions} test functions: stale."
    )


# ─── What the docs must mention ─────────────────────────────────────────────

@pytest.mark.parametrize("feature,needle", [
    ("ייצוא מלא",       "/account.json"),
    ("תפוגת קוד הזמנה", "expire"),
    ("שימור הארכיון",   "pg_cron"),
    ("פרויקטים",        "Projects"),
    ("סריקת קבלות",     "Receipt scanning"),
    ("תקציבי קטגוריות", "budget"),
    ("תפקיד מנהל",      "manager"),
    ("ייצוא CSV",       "CSV"),
    ("בדיקות בריאות",   "/health"),
])
def test_the_readme_mentions_every_major_feature(feature, needle):
    """A feature that exists and isn't documented is a feature nobody will find."""
    assert needle.lower() in _README.lower(), f"{feature} isn't mentioned in the README"


# A section heading and not a single word: "פרויקט" also appears in passing
# mentions, so deleting the whole section would pass silently.
@pytest.mark.parametrize("feature,heading", [
    ("שמירה וייצוא",    "ייצוא"),
    ("פרויקטים",        "פרויקטים"),
    ("סריקת קבלות",     "סריקת קבלות"),
    ("הרשאות",          "הרשאות"),
])
def test_the_spec_has_a_section_for_every_major_feature(feature, heading):
    headings = re.findall(r"^#{2,3} .*$", _SPEC, re.M)
    assert any(heading in h for h in headings), (
        f"SPEC.md has no section on {feature}. The sections it has: {headings}"
    )


def test_the_spec_describes_budgets():
    assert "תקציב חודשי" in _SPEC, "the budgets aren't described in SPEC.md"


def test_the_spec_is_not_stuck_on_four_pages():
    """The wording "4 pages" survived three features that added pages of their own."""
    assert "## 4 עמודים" not in _SPEC


# ─── What must not be in the docs ───────────────────────────────────────────

def test_no_real_account_details_are_published():
    """`SPEC.md` held two real accounts by name, with a note that they share a
    password. The repo is public. The password itself isn't there, and half the
    work is."""
    docs = {"README.md": _README, "docs/SPEC.md": _SPEC}
    for name, text in docs.items():
        found = re.findall(r"[A-Za-z0-9._%+-]+@(?!example\.|smartfin\.test)"
                           r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        # The product's contact address is a deliberate publication, not a leak.
        leaked = [e for e in found if e != "matanshneor1@gmail.com"]
        assert not leaked, f"{name} publishes real addresses: {leaked}"

    assert "סיסמה משותפת" not in _SPEC


# An Israeli mobile phone pattern. The previous check scrubbed **emails** only, and
# two real numbers of real users sat in the public repo with the note "a real
# number from the database", while the database has a function, open to anon,
# that turns a phone into an email address. The guard built to catch exactly this
# missed it.
_ISRAELI_MOBILE = re.compile(r"\b0?5[0-9][- ]?\d{3}[- ]?\d{4}\b")

# The numbers allowed to appear: all 1234567 / 9876543, and clearly nobody's.
_OBVIOUSLY_FAKE = re.compile(r"(1234567|9876543|0000000|1111111)")


@pytest.mark.parametrize("where", ["tests", "backend", "docs", "frontend"])
def test_no_real_phone_number_is_published(where):
    """A user's real number in a public repo is half the work for someone trying to
    get into their account."""
    leaked = []
    for path in sorted((_ROOT / where).rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".html", ".js", ".md", ".sql"):
            continue
        for m in _ISRAELI_MOBILE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            # Compare digits only: `54-123-4567` and `0541234567` are the same
            # number, and the separators vary by context.
            if not _OBVIOUSLY_FAKE.search(re.sub(r"[- ]", "", m.group(0))):
                leaked.append(f"{path.relative_to(_ROOT)}: {m.group(0)}")

    assert not leaked, "phone numbers that aren't clearly made up:\n  " + "\n  ".join(leaked)


def test_the_docs_do_not_claim_rate_limiting_is_memory_only():
    """The claim "in-memory, auth routes only" was wrong in both its parts, and in
    the dangerous direction: it describes weaker protection than what exists, so
    whoever reads it may "fix" exactly what already works."""
    assert "in-memory, auth routes only" not in _README


def test_the_readme_does_not_send_people_to_deploy_around_ci():
    """`railway up` bypasses the CI gate. It still exists, but the docs must say it
    bypasses it; otherwise it is presented as the parallel, equivalent way."""
    if "railway up" in _README:
        window = _README[_README.index("railway up"):][:400]
        assert "skips" in window or "עוקף" in window, \
            "railway up is documented without saying it skips the CI checks"


# ─── The operational warnings ───────────────────────────────────────────────

# Three warnings that already caused a real incident, none of which is evident
# from reading the code. The check looks for the warning's **body** and not the
# file name: "clock.py" also appears in the directory tree, so deleting the
# warning itself broke nothing.
_LANDMINES = [
    ("worker אסינכרוני", ["--threads", "gevent", "singleton"]),
    ("שעון ישראל",       ["Asia/Jerusalem", "UTC", "clock.today()"]),
    ("אפס מדומה",        ["DataUnavailable", "false zero"]),
]


@pytest.mark.parametrize("name,phrases", _LANDMINES, ids=[m[0] for m in _LANDMINES])
def test_the_readme_carries_the_three_landmines(name, phrases):
    missing = [p for p in phrases if p not in _README]
    assert not missing, f"the warning about {name} was worn down: missing from the README: {missing}"


def test_the_readme_does_not_promise_a_session_length_the_code_does_not_use():
    """The README said 90 days while the code said 3,650. The same kind of gap this
    file was written to catch, only in the number that decides how long a stolen
    phone stays signed in."""
    from backend.app import app

    days = app.permanent_session_lifetime.days
    assert f"{days}-day session" in _README, \
        f"the code says {days} days; the README says something else"


@pytest.mark.parametrize("name,phrase", [
    ("worker אסינכרוני", "--threads"),
    ("שעון ישראל",       "clock.now_utc()"),
    ("אפס מדומה",        "DataUnavailable"),
])
def test_the_spec_repeats_the_three_landmines_in_hebrew(name, phrase):
    """The same three warnings as in the README, in Hebrew. `SPEC.md` is the
    document Matan reads, and a warning that exists only in English is a warning
    half the readers will miss."""
    assert phrase in _SPEC, f"the warning about {name} is missing from SPEC.md"
