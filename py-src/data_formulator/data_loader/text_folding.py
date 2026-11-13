# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fold Arabic-script spelling variants so Persian text compares equal.

Persian data is routinely stored with a mix of code points that look identical
on screen: Arabic ``ي``/``ك`` next to Persian ``ی``/``ک`` (legacy keyboards and
Windows-1256 imports), Persian or Arabic-Indic digits next to ASCII digits, and
a zero-width non-joiner where another row has a plain space. A byte-exact
``WHERE famil = 'کریمی'`` then silently drops every row typed the other way.

This module provides the same folding on both sides of a comparison:

* :func:`fold_text` for Python strings (catalog search, profiling);
* :func:`sql_fold` for a SQL expression, as nested ``REPLACE`` calls that work
  on MySQL, PostgreSQL and DuckDB without extensions.
"""

from __future__ import annotations

import re

# Character -> canonical replacement. Order is irrelevant (single code points).
_LETTER_FOLDS: dict[str, str] = {
    "ي": "ی",  # ARABIC YEH            -> FARSI YEH
    "ى": "ی",  # ARABIC ALEF MAKSURA   -> FARSI YEH
    "ك": "ک",  # ARABIC KAF            -> KEHEH
    "ۀ": "ه",  # HEH WITH YEH ABOVE    -> HEH
    "ة": "ه",  # TEH MARBUTA           -> HEH
}
_SPACE_FOLDS: dict[str, str] = {
    "‌": " ",  # ZERO WIDTH NON-JOINER
    "‍": "",   # ZERO WIDTH JOINER
    " ": " ",  # NO-BREAK SPACE
    "ـ": "",   # TATWEEL
}
_DIGIT_FOLDS: dict[str, str] = {
    **{chr(0x06F0 + i): str(i) for i in range(10)},  # Persian digits
    **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic digits
}
_HARAKAT_RE = re.compile(r"[ً-ْٰ]")
_WS_RE = re.compile(r"\s+")

_PY_TABLE = str.maketrans({**_LETTER_FOLDS, **_SPACE_FOLDS, **_DIGIT_FOLDS})

_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿ‌‍]")
_NON_ASCII_DIGIT_RE = re.compile(r"[٠-٩۰-۹]")
_ANY_DIGIT_RE = re.compile(r"[0-9٠-٩۰-۹]")


def fold_text(value: str) -> str:
    """Return *value* with Arabic-script variants folded to one spelling."""
    if not value:
        return value
    out = _HARAKAT_RE.sub("", value.translate(_PY_TABLE))
    return _WS_RE.sub(" ", out).strip()


def has_arabic_script(value: object) -> bool:
    return isinstance(value, str) and _ARABIC_SCRIPT_RE.search(value) is not None


def fold_plan(value: object, *, substring: bool = False) -> tuple[bool, bool]:
    """Decide which fold groups a filter value needs: ``(letters, digits)``.

    Folding the column side defeats indexes, so it is applied only when the
    value could actually be affected:

    * letters/spaces — the value contains Arabic-script characters;
    * digits — the value contains non-ASCII digits, or it is a substring match
      (already unindexed) or Arabic-script text that contains any digit.
      A plain ASCII value such as ``'1402/01/01'`` on an equality filter is left
      alone so date-string columns keep using their index.
    """
    if not isinstance(value, str) or not value:
        return False, False
    letters = _ARABIC_SCRIPT_RE.search(value) is not None
    digits = _NON_ASCII_DIGIT_RE.search(value) is not None or (
        (substring or letters) and _ANY_DIGIT_RE.search(value) is not None
    )
    return letters, digits


def _sql_str(ch: str) -> str:
    return "'" + ch.replace("'", "''") + "'"


def sql_fold(expr: str, *, letters: bool = True, digits: bool = True) -> str:
    """Wrap SQL *expr* in ``REPLACE`` calls equivalent to :func:`fold_text`.

    Whitespace collapsing and diacritic removal are not replicated in SQL; they
    are rare in stored values and would need regex support.
    """
    pairs: list[tuple[str, str]] = []
    if letters:
        pairs += list(_LETTER_FOLDS.items()) + list(_SPACE_FOLDS.items())
    if digits:
        pairs += list(_DIGIT_FOLDS.items())
    out = expr
    for src, dst in pairs:
        out = f"REPLACE({out}, {_sql_str(src)}, {_sql_str(dst)})"
    return out


def fold_literal(value: str, *, letters: bool = True, digits: bool = True) -> str:
    """Fold a filter value the same way :func:`sql_fold` folds the column."""
    table: dict[str, str] = {}
    if letters:
        table.update(_LETTER_FOLDS)
        table.update(_SPACE_FOLDS)
    if digits:
        table.update(_DIGIT_FOLDS)
    return value.translate(str.maketrans(table))


# Dialects whose string functions and literals are known to handle the folding
# SQL above. Others (SQL Server needs N'…' literals, Kusto is not SQL) are left
# byte-exact rather than risk a broken query.
FOLDING_DIALECTS = frozenset({"mysql", "postgres", "postgresql", "duckdb", "ansi"})
