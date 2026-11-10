"""Order in settings (Matan, 11.10): "is this mine, or the family's?"

Until then the name and workplace were edited from a pencil in the family member
list, the colour from a button on the same row, and email, phone and password in a
third group, "אבטחה וחשבון". Now: "החשבון שלי": everything that belongs only to me;
"המשפחה שלי": what is shared, with a read-only family member list. The order: the
family first, then by how often each group is visited, and "החשבון שלי" last, above
"התנתקות".
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HTML = (Path(__file__).resolve().parent.parent / "frontend/templates/settings.html").read_text(encoding="utf-8")


def _group(anchor):
    body = _HTML[_HTML.index(anchor):]
    return body[:body.index("</section>")]


def test_everything_of_mine_is_in_my_details():
    mine = _group('id="my-details"')
    for el in ("editProfileBtn", "memberColorOpen", "toggleEmailBtn", "togglePhoneBtn",
               "togglePasswordBtn", "toggleDeleteAccountBtn"):
        assert f'id="{el}"' in mine, el


def test_the_family_group_holds_what_is_shared():
    family = _group('id="family-settings"')
    for el in ("editFamilyNameBtn", "copyCodeBtn", "toggleFamilyMoveBtn",
               "toggleResetAccountBtn", "removeMemberBtn"):
        assert f'id="{el}"' in family, el
    # the list is read-only: no editing of my details from within it
    members = family[family.index('class="members-settings-list"'):]
    members = members[:members.index("</ul>")]
    assert "<button" not in members


def test_the_groups_come_in_matans_order():
    import re
    titles = re.findall(r'<span class="group-title">([^<]+)</span>', _HTML)
    assert titles == ["המשפחה שלי", "קטגוריות", "עסקאות קבועות", "שיוך עסקאות",
                      "תצוגה", "התראות", "החשבון שלי"]
    # "החשבון שלי" right above "התנתקות"
    assert _HTML.index('id="my-details"') < _HTML.index('class="logout-form') 
    assert '<span class="group-title">אבטחה וחשבון</span>' not in _HTML


def test_rare_actions_are_folded_away():
    """Deleting an account, resetting transactions and removing a family member: under
    "אפשרויות מתקדמות"."""
    for anchor, el in (('id="my-details"', "toggleDeleteAccountBtn"),
                       ('id="family-settings"', "toggleResetAccountBtn"),
                       ('id="family-settings"', "removeMemberBtn")):
        group = _group(anchor)
        folded = group[group.index('<details class="advanced-options">'):]
        assert f'id="{el}"' in folded, el
