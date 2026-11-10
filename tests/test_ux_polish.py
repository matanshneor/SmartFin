"""Four things that worked correctly and felt wrong.

None of them was a bug: the app calculated correctly, saved correctly and
displayed correctly. They simply made it harder for a person to do what they came
to do.

1. **An irrelevant field in second place.** "שייך לפרויקט" was always shown, even
   for a family with no project at all, with one option: "ללא". In an action done
   ten times a day, two redundant fields are a lot.
2. **The central feature was inaccessible from the place that leads to it.** A
   person sees ₪1,240 on groceries and thinks "I should set a limit", and the
   month page had no pointer at all to settings.
3. **Two forms of address, sometimes on the same screen.** The hero says
   "לחצו... שלכם" and the list below it "לחץ".
4. **Every new user's first number was a red minus.** Almost everyone enters an
   expense before a salary. Accounting-wise that is right; in practice the app
   opens with a message that something is wrong.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


def _strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def _strip_jinja(html):
    return re.sub(r"\{#.*?#\}", "", html, flags=re.S)


# ─── 1. The project field is hidden when there is nowhere to attribute to ────

def test_the_project_field_hides_itself_when_there_are_no_projects():
    """The heart. A field with one option ("ללא") in second place of the most common
    form."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    fn = js[js.index("function buildProjectSelect"):]
    fn = fn[:fn.index("\n    }")]

    assert re.search(r"projectGroup\.style\.display\s*=\s*projects\.length", fn), \
        "the field is shown even when there are no projects"


def test_it_is_not_forced_visible_somewhere_else():
    """setType set display = '' unconditionally, and that would override any hiding."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))

    assert "projectGroup.style.display = '';" not in js


def test_the_owner_field_still_hides_the_same_way():
    """Control: the pattern I copied from still exists."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))

    assert "ownerGroup.style.display = (hasOwner && !window.SF_PAGE_DATA.singleMember) ? '' : 'none';" in js


# ─── 2. A budget can be set from the place where you realise it's needed ─────

def test_the_month_page_offers_to_set_a_budget():
    """Until now there was **no** pointer at all from the month page to settings.
    Since then (Matan, 30.9) it is one button opening a window for all categories:
    not a link after each row."""
    html = _read("frontend/templates/month.html")

    assert 'id="budgetsOpen"' in html
    assert "cat-budget-cta" not in html
    assert "קביעת תקציב לקטגוריה" not in html


def test_the_budget_button_only_appears_in_the_current_month():
    """The budget is saved per category and not per month: from March it would apply
    from today on."""
    html = _read("frontend/templates/month.html")
    before = html[:html.index('id="budgetsOpen"')]

    assert before.rstrip().endswith('<button type="button" class="btn-sm btn-ghost"')
    assert before[before.rindex("{%"):].startswith("{% if is_current %}")


def test_the_window_only_sends_what_changed():
    js = _strip_comments(_read("frontend/static/js/month.js"))
    part = js[js.index("closest('#budgetsOpen')") - 3000:]

    assert "if (v === inp.dataset.old) return;" in part
    assert "limits[inp.dataset.cat] = null" in part          # empty = removal


def test_the_old_per_category_link_left_nothing_behind():
    """Matan (1.10): the "קביעת תקציב" link next to each category was replaced by a
    window (30.9), but the code waiting for it in settings (#budget-<id>) and its
    styling remained."""
    assert "#budget-" not in _read("frontend/static/js/settings.js")
    css = _read("frontend/static/css/style.css")
    assert ".cat-budget-cta" not in css and "just-landed" not in css


# ─── 3. One form of address ──────────────────────────────────────────────────

# Masculine singular address in running text.
#
# **Button labels are excluded on purpose.** "שמור" and "מחק" on a button are a
# short, accepted imperative, and the app mixes them anyway with action nouns
# ("ביטול", "הצטרפות", "פתיחת חשבון"): that is a style question predating this
# fix and not part of it. What does break is an **explanation** addressing the
# user in two different forms on the same screen.
_SINGULAR = ["לחץ", "הזן", "בחר", "סמן", "הוסף", "נסה"]

# Hebrew word boundaries: without them "הכ**נסה**" and "ל**סמן**" count as singular
# address, and the test reports five valid files.
_SINGULAR_RE = re.compile(
    r"(?<![\w\u0590-\u05FF])(" + "|".join(_SINGULAR) + r")(?![\w\u0590-\u05FF])")


def _prose(html):
    """Running text only: without button and link labels."""
    html = _strip_jinja(html)
    html = re.sub(r"<(button|a)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html)


@pytest.mark.parametrize("template", sorted(p.name for p in (_ROOT / "frontend/templates").glob("*.html")))
def test_no_template_addresses_the_user_in_the_singular(template):
    """The app is for a family, and it speaks in the plural everywhere else. Two forms
    of address on the same screen read like two writers."""
    found = sorted({m.group(1) for m in
                    _SINGULAR_RE.finditer(_prose(_read(f"frontend/templates/{template}")))})

    assert not found, f"{template} uses singular address: {found}"


def test_the_dashboard_says_the_same_thing_twice_the_same_way():
    """The most prominent of all: the hero and the list below it, the same screen, the
    same action.

    Since 31.10 the new-family hero doesn't explain where the + is: it is a button
    opening the same window (data-add-type), with import next to it. The textual
    instruction remained only in the list."""
    html = _strip_jinja(_read("frontend/templates/index.html"))

    assert html.count("לחצו על +") == 1
    hero = html[html.index('class="hero-balance hero-start"'):]
    hero = hero[:hero.index("</section>")]
    assert 'data-add-type="expense"' in hero and "import_page" in _read("frontend/templates/index.html")


# ─── 4. A month with no income isn't a deficit ───────────────────────────────

# "A month with no income isn't a deficit" is now tested by behaviour, on the
# rendered home page:
# tests/test_minus_zero.py::test_a_month_with_no_income_is_not_shown_as_a_deficit.
# The test that sat here counted occurrences of summary.remaining < 0 in the source
# code, and that stopped matching when the condition moved to one variable
# (deficit).

def test_it_says_what_the_number_means_instead():
    """"נשאר בעו״ש" on a number with nothing to subtract from is an untrue sentence."""
    html = _read("frontend/templates/index.html")

    assert "יצא החודש" in html
    assert "עדיין לא הוזנו הכנסות החודש" in html


def test_the_normal_case_is_untouched():
    """Control: whoever has income sees exactly what they saw before."""
    html = _read("frontend/templates/index.html")

    # The label users see (Matan, 1.10). This used to look for "נשאר בעו״ש החודש",
    # which survived only inside a template comment after the label changed.
    assert "'מאזן החודש'" in html
    assert "מתוך ₪{{ summary.income | money }} הכנסות החודש" in html


# ─── 5. The dashboard bar mixed savings with expenses ───────────────────────
#
# used_pct = (expense + savings) / income, and the label was "נוצלו X%". A month
# where you spent 20% of income and saved 69% showed as an **almost full** gold bar
# with "נוצלו 89%": that is, an excellent month that looked like a warning. Money
# saved wasn't used; it moved.
#
# And worse: {% if used_pct > 90 %}danger{% endif %}: a bit more saving and the bar
# would turn red. A warning about good behaviour.

def test_the_bar_does_not_add_savings_to_spending():
    """The heart. These two opposite things can't share one number."""
    html = _read("frontend/templates/index.html")

    assert "used_pct" not in html, "the merged number still exists"
    assert "spent_pct" in html and "saved_pct" in html


def test_spending_is_measured_against_income_alone():
    html = _read("frontend/templates/index.html")
    line = next(l for l in html.split("\n") if "set spent_pct" in l)

    assert "summary.expense / summary.income" in line
    assert "savings" not in line


def test_the_red_warning_follows_spending_and_not_the_total():
    """This is what should really worry. Before, large savings were enough to turn the
    bar red."""
    html = _read("frontend/templates/index.html")
    spent_fill = next(l for l in html.split("\n") if "balance-bar-fill spent" in l)
    saved_fill = next(l for l in html.split("\n") if "balance-bar-fill saved" in l)

    assert "spent_pct > 90" in spent_fill and "danger" in spent_fill
    assert "danger" not in saved_fill
    assert "saved_pct" not in spent_fill, "savings went back into the red condition"


def test_both_parts_are_drawn_separately():
    """One bar in one colour can't show a split."""
    html = _read("frontend/templates/index.html")
    bar = html[html.index("balance-bar-split"):]
    bar = bar[:bar.index("balance-bar-legend")]

    assert 'balance-bar-fill spent' in bar
    assert 'balance-bar-fill saved' in bar


def test_the_two_parts_cannot_overflow_the_track():
    """If expenses and savings together exceed 100% (possible: spending from savings),
    the second part must shrink and not push the bar out."""
    html = _read("frontend/templates/index.html")
    saved = next(l for l in html.split("\n") if 'balance-bar-fill saved' in html and "saved_pct" in l and "width" in l)

    assert "100 - [spent_pct, 100] | min" in saved


def test_the_legend_says_which_colour_is_which():
    """Two colours without a key are a guess."""
    html = _read("frontend/templates/index.html")
    legend = html[html.index("balance-bar-legend"):]
    legend = legend[:legend.index("</div>")]

    assert 'bar-key spent' in legend and 'bar-key saved' in legend
    assert "נוצלו" not in legend


def test_the_legend_says_a_percent_of_what():
    """"הוצאות 20%" doesn't say a percentage of what. The line above the bar does
    ("מתוך ₪11,527 הכנסות החודש"), but you can't ask someone to combine two separate
    lines in their head, and that is exactly what Matan asked."""
    html = _read("frontend/templates/index.html")
    legend = html[html.index("balance-bar-legend"):]
    legend = legend[:legend.index("</div>")]

    assert "מההכנסות" in legend, "the key doesn't say a percentage of what"

    spent_line = next(l for l in legend.split("\n") if "spent_pct" in l)
    assert "מההכנסות" in spent_line, "missing precisely on expenses: the number that misled"


def test_the_screen_reader_hears_the_same_sentence():
    """A screen reader doesn't see the line above the bar, so aria-label is the only
    place the denominator can be told to it."""
    html = _read("frontend/templates/index.html")
    label = next(l for l in html.split("\n") if "aria-label=" in l and "spent_pct" in l)

    assert "מההכנסות" in label


def test_the_screen_reader_hears_the_spending_number():
    """"התקדמות 89%" on a month where you spent 20% is the same lie, out loud."""
    html = _read("frontend/templates/index.html")
    bar = html[html.index("balance-bar-split") - 200:]
    bar = bar[:bar.index("</div>")]

    assert 'aria-valuenow="{{ spent_pct }}"' in bar


# ─── 6. And both parts were the same colour ─────────────────────────────────
#
# The split was written correctly and the bar looked **exactly as before**: .spent got
# #C79A18 and .saved got #C79A18 at 38% opacity, 8 pixels tall. Matan looked at the
# screen and said "I didn't see a change", and he was right.
#
# The seven tests above all passed. They checked the markup is split, not that
# anyone can see the split. That is what this part adds.

def _css_rule(css, selector):
    """The rule's position and body. Anchored to the start of a line; otherwise
    .balance-bar-fill.spent is also found inside .page-hero .balance-bar-fill.spent,
    and every check on the general rule is mistakenly answered by the hero's. A
    mutation survived exactly like that."""
    import re
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    assert m, f"no such rule: {selector}"
    return m.start(), m.group(1)


def _css_decl(css, selector, prop="background"):
    """The declared value of a property in a specific CSS rule."""
    import re
    d = re.search(rf"\b{prop}\s*:\s*([^;]+)", _css_rule(css, selector)[1])
    return d.group(1).strip() if d else None


def test_the_two_halves_are_not_the_same_colour():
    """The heart. The same colour on both parts makes the split invisible."""
    css = _read("frontend/static/css/style.css")

    for scope in ("", ".page-hero "):
        spent = _css_decl(css, f"{scope}.balance-bar-fill.spent")
        saved = _css_decl(css, f"{scope}.balance-bar-fill.saved")
        assert spent and saved
        assert spent != saved, f"both parts are the same colour in {scope or 'default'}"


def test_the_legend_dots_match_their_segments():
    """A dot in a different colour from the part it marks is worse than no dot."""
    css = _read("frontend/static/css/style.css")

    for scope in ("", ".page-hero "):
        for part in ("spent", "saved"):
            fill = _css_decl(css, f"{scope}.balance-bar-fill.{part}")
            key = _css_decl(css, f"{scope}.balance-bar-legend .bar-key.{part}")
            assert fill == key, f"{scope}{part}: the bar {fill}, the dot {key}"


def test_the_danger_colour_still_wins():
    """.spent got its own colour after the danger rule was already written: that is,
    the normal colour overrode the red, and the overrun warning silently vanished."""
    css = _read("frontend/static/css/style.css")

    for scope in ("", ".page-hero "):
        plain = _css_rule(css, f"{scope}.balance-bar-fill.spent")[0]
        danger = _css_rule(css, f"{scope}.balance-bar-fill.spent.danger")[0]
        assert danger > plain, f"the red rule in {scope or 'default'} comes too early and so loses"


def test_the_split_bar_is_tall_enough_to_read():
    """At 8 pixels tall two parts look like one."""
    css = _read("frontend/static/css/style.css")
    height = _css_decl(css, ".balance-bar-split", "height")

    assert height and int(height.replace("px", "").split()[0]) >= 10


def test_there_is_a_visible_seam_between_the_halves():
    """Even if the colours get closer one day, the gap keeps the split."""
    css = _read("frontend/static/css/style.css")

    assert _css_decl(css, ".balance-bar-split", "gap")
