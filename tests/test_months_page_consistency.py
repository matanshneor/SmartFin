"""
Tests for the consistency of the month comparison page.

A transaction attached to a project (a trip, a renovation) is a one-off expense
that distorts the picture of a "normal month", so it is deliberately excluded
from every monthly balance in the app: a decision documented in migration
20260804140000.

The /months page shows two things about the same months: a bar chart on top and
an archive table below. The table was fixed to exclude projects; the chart was
forgotten. The same July appeared as ₪31,400 in the chart and ₪9,400 in the
table, two centimetres apart, with no explanation: it simply looked like an app
that calculates wrong.
"""
import re

import pytest


pytestmark = pytest.mark.unit

_PROJECT_FILTER = '.is_("project_id", "null")'


# The trend chart was fetched separately until 27.9.2026, and the three tests
# that sat here checked that its query excludes projects like the table. It is
# now derived from the table itself (db.monthly_trend(archive)), so their
# agreement is structural, and tested by behaviour in tests/test_row_cap.py.
# What is left to check here is the single source.


def test_the_archive_function_also_excludes_them():
    """The third side of the same page sits in SQL and not in Python, so it is tested
    separately; otherwise "everyone agrees" might be true for only two of three."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent
           / "backend/supabase/migrations"
           / "20260804140000_months_archive_exclude_projects.sql").read_text(encoding="utf-8")

    assert re.search(r"project_id\s+IS\s+NULL", sql, re.IGNORECASE)
