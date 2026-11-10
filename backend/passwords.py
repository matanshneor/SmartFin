"""What a new password must satisfy (Matan, 10.11.2026).

For every new password (signup, reset, and change in settings):

  · At least 8 characters.
  · At least one letter (Hebrew or English) and at least one digit.

An existing password is never checked: anyone already registered keeps signing
in exactly as before, with no request to change it.

The leaked-password check (Pwned Passwords) that was here briefly was removed
at Matan's request, the same day: these two rules and nothing else.
"""
import re

MIN_LENGTH = 8

TOO_SHORT = f"הסיסמה חייבת להכיל לפחות {MIN_LENGTH} תווים"
NO_LETTER = "הסיסמה חייבת להכיל לפחות אות אחת"
NO_DIGIT = "הסיסמה חייבת להכיל לפחות ספרה אחת"

# Any letter in any script, not only a-z: a Hebrew password is a valid password
_LETTER = re.compile(r"[^\W\d_]")
_DIGIT = re.compile(r"[0-9]")


def problem(password: str):
    """The error message for a new password, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return TOO_SHORT
    if not _LETTER.search(password):
        return NO_LETTER
    if not _DIGIT.search(password):
        return NO_DIGIT
    return None
