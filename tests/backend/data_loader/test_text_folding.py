"""Persian/Arabic spelling-variant folding for filters and catalog search."""
from __future__ import annotations

import duckdb
import pytest

from data_formulator.data_loader import probe_utils, text_folding as tf
from data_formulator.data_loader.external_data_loader import (
    build_source_filter_where_clause_inline,
)

pytestmark = [pytest.mark.backend]

FA = "کریمی"          # Persian keheh + Farsi yeh
AR = "كريمي"          # Arabic kaf + Arabic yeh


def test_fold_text_unifies_letter_digit_and_space_variants():
    assert tf.fold_text(AR) == FA
    assert tf.fold_text("۰۹۱۲٣") == "09123"
    assert tf.fold_text("کم‌چرب") == "کم چرب"
    assert tf.fold_text("  نامـ  خانوادگی ") == "نام خانوادگی"


def test_fold_plan_leaves_plain_ascii_equality_alone():
    # An ASCII date string on EQ must not defeat the column's index.
    assert tf.fold_plan("1402/01/01") == (False, False)
    assert tf.fold_plan("1402/01/01", substring=True) == (False, True)
    assert tf.fold_plan("۱۴۰۲/۰۱/۰۱")[1] is True
    assert tf.fold_plan(FA) == (True, False)
    assert tf.fold_plan(42) == (False, False)


@pytest.mark.parametrize("value", [FA, AR])
def test_source_filter_matches_both_spellings(value):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (famil VARCHAR)")
    con.execute("INSERT INTO t VALUES (?), (?), ('احمدی')", [FA, AR])
    where = build_source_filter_where_clause_inline(
        [{"column": "famil", "operator": "EQ", "value": value}], quote_char='"', dialect="duckdb",
    )
    assert con.execute(f"SELECT count(*) FROM t {where}").fetchone()[0] == 2


def test_source_filter_in_and_ilike_fold_digits_and_zwnj():
    con = duckdb.connect()
    con.execute("CREATE TABLE t (mobile VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO t VALUES ('09121234567', 'شیر کم‌چرب'), ('۰۹۱۲۷۶۵۴۳۲۱', 'ماست')")
    w = build_source_filter_where_clause_inline(
        [{"column": "mobile", "operator": "ILIKE", "value": "0912"}], quote_char='"', dialect="duckdb")
    assert con.execute(f"SELECT count(*) FROM t {w}").fetchone()[0] == 2
    w = build_source_filter_where_clause_inline(
        [{"column": "name", "operator": "ILIKE", "value": "کم چرب"}], quote_char='"', dialect="duckdb")
    assert con.execute(f"SELECT count(*) FROM t {w}").fetchone()[0] == 1
    w = build_source_filter_where_clause_inline(
        [{"column": "name", "operator": "IN", "value": ["ماست", "شير کم چرب"]}], quote_char='"', dialect="duckdb")
    assert con.execute(f"SELECT count(*) FROM t {w}").fetchone()[0] == 2


def test_unaffected_filters_compile_unchanged():
    w = build_source_filter_where_clause_inline(
        [{"column": "tarikh", "operator": "EQ", "value": "1402/01/01"},
         {"column": "n", "operator": "GT", "value": 5}], quote_char="`", dialect="mysql")
    assert w == "WHERE `tarikh` = '1402/01/01' AND `n` > 5"


def test_folding_skipped_for_unsupported_dialect():
    w = build_source_filter_where_clause_inline(
        [{"column": "famil", "operator": "EQ", "value": FA}], quote_char='"', dialect="mssql")
    assert "REPLACE" not in w


def test_source_filter_accepts_op_alias():
    # probe_data filters use "op"; the same key must not be silently dropped here.
    w = build_source_filter_where_clause_inline(
        [{"column": "vaziat", "op": "GTE", "value": 3}], quote_char="`", dialect="mysql")
    assert w == "WHERE `vaziat` >= 3"


def test_probe_where_folds_and_accepts_operator_alias():
    w = probe_utils._compile_where(
        [{"column": "famil", "operator": "EQ", "value": AR}], probe_utils.MYSQL)
    assert "REPLACE" in w and FA in w and AR not in w.split("=")[-1]
