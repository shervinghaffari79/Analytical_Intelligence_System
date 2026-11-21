"""User-provided schema files: parsing, validation, overlay, hiding, template."""
from __future__ import annotations

import copy

import pytest

from data_formulator.datalake import catalog_schema as cs
from data_formulator.datalake.catalog_cache import (
    list_path_children, list_sources_summary, load_catalog, save_catalog, search_catalog_cache,
)

pytestmark = [pytest.mark.backend]

YAML = """
version: 1
name: Pakhsh ERP
language: fa
description: سامانه فروش و انبار یک شرکت پخش مواد غذایی
rules:
  - "Amounts are in Rials; divide by 10 for Toman."
  - "تاریخ‌ها شمسی و به صورت رشته YYYY/MM/DD هستند"
glossary:
  - {term: مشتری, aka: [customer, خریدار], maps_to: [tbl_moshtari], meaning: خریدار کالا}
examples:
  - {question: فروش ماهانه, tables: [tbl_faktor], note: join on moshtari_id}
tables:
  tbl_faktor:
    description: فاکتورهای فروش
    aka: [invoice, فاکتور]
    grain: one row per invoice
    columns:
      vaziat:
        description: وضعیت فاکتور
        values: {1: تایید شده, 2: در انتظار پرداخت, 3: لغو شده}
        lookup: {table: tbl_code_paye, key: code, label: sharh, where: {noe: vaziat_faktor}}
      tarikh: {description: تاریخ فاکتور, type: jalali_date}
      jam_kol: {description: مبلغ کل, unit: ریال}
      moshtari_id: {description: شناسه مشتری, references: tbl_moshtari.moshtari_id}
  tbl_moshtari: مشتریان فروشگاه
  tbl_log:
    ignore: true
  tbl_nope: this table is not in the database
"""


def _catalog() -> list[dict]:
    def table(key, cols, **meta):
        return {"name": key, "table_key": f"db.{key}", "path": ["db", key],
                "metadata": {"columns": [{"name": c, "type": "int"} for c in cols], **meta}}
    return [
        table("tbl_faktor", ["faktor_id", "moshtari_id", "tarikh", "vaziat", "jam_kol"],
              description="generated guess", description_source="auto", row_count=12000),
        table("tbl_moshtari", ["moshtari_id", "famil"], description="comment from the DBA"),
        table("tbl_code_paye", ["noe", "code", "sharh"]),
        table("tbl_log", ["log_id"]),
        table("tbl_kala", ["kala_id"]),
    ]


def _schema():
    schema, issues = cs.normalize_schema(cs.parse_schema_text(YAML, "x.yaml"))
    assert not [i for i in issues if i["level"] == "error"], issues
    return schema


# ---------------------------------------------------------------- parsing

def test_yaml_json_and_csv_all_produce_the_same_kind_of_schema():
    s = _schema()
    assert s["tables"]["tbl_faktor"]["columns"]["vaziat"]["values"] == {"1": "تایید شده", "2": "در انتظار پرداخت", "3": "لغو شده"}
    assert s["tables"]["tbl_moshtari"]["description"] == "مشتریان فروشگاه"   # one-line shorthand
    js, _ = cs.normalize_schema(cs.parse_schema_text('{"tables": {"t": {"description": "d"}}}', "a.json"))
    assert js["tables"]["t"]["description"] == "d"
    csv_text = ("table,column,description,type,values,table_description\n"
                "tbl_faktor,vaziat,وضعیت,code,1=تایید; 2=لغو,فاکتورها\n"
                "tbl_faktor,tarikh,تاریخ,jalali_date,,\n")
    cv, issues = cs.normalize_schema(cs.parse_schema_text(csv_text, "dictionary.csv"))
    assert cv["tables"]["tbl_faktor"]["description"] == "فاکتورها"
    assert cv["tables"]["tbl_faktor"]["columns"]["vaziat"]["values"] == {"1": "تایید", "2": "لغو"}
    assert cv["tables"]["tbl_faktor"]["columns"]["tarikh"]["type"] == "jalali_date"
    assert not issues


def test_unreadable_files_get_a_precise_message():
    with pytest.raises(cs.SchemaFileError, match=r"Invalid YAML.*line"):
        cs.parse_schema_text("tables:\n  a: [unclosed\n  b: 1", "x.yaml")
    with pytest.raises(cs.SchemaFileError, match="Invalid JSON"):
        cs.parse_schema_text("{nope", "x.json")
    with pytest.raises(cs.SchemaFileError, match="mapping"):
        cs.parse_schema_text("- just\n- a list", "x.yaml")
    with pytest.raises(cs.SchemaFileError, match="`table` and `column`"):
        cs.parse_schema_text("a,b\n1,2", "x.csv")
    with pytest.raises(cs.SchemaFileError, match="larger"):
        cs.parse_schema_text("x" * (cs.MAX_SCHEMA_BYTES + 1), "x.yaml")


def test_structural_mistakes_are_reported_with_their_path():
    _, issues = cs.normalize_schema({"tables": {"t": {"columns": {"c": {"values": [1, 2], "type": "bogus", "frobnicate": 1}}},
                                                "u": 5}, "mystery": 1, "glossary": [{"aka": ["x"]}]})
    by_path = {i["path"]: i for i in issues}
    assert by_path["tables.t.columns.c.values"]["level"] == "error"
    assert by_path["tables.u"]["level"] == "error"
    assert by_path["glossary[0]"]["level"] == "error"
    assert by_path["tables.t.columns.c.type"]["level"] == "warning"
    assert by_path["tables.t.columns.c.frobnicate"]["level"] == "warning"
    assert by_path["mystery"]["level"] == "warning"
    _, issues = cs.normalize_schema({"version": 1})
    assert any(i["path"] == "tables" and i["level"] == "error" for i in issues)


# ------------------------------------------------------- matching / checking

def test_matching_accepts_short_full_and_case_variants_and_suggests_for_typos():
    schema = _schema()
    matches, issues = cs.match_tables(schema, _catalog())
    assert set(matches) == {"tbl_faktor", "tbl_moshtari", "tbl_log"}
    nope = [i for i in issues if i["path"] == "tables.tbl_nope"][0]
    assert nope["level"] == "warning" and "no such table" in nope["message"]

    typo, _ = cs.normalize_schema({"tables": {"tbl_fakter": {"description": "x"}}})
    _, issues = cs.match_tables(typo, _catalog())
    assert "tbl_faktor" in issues[0]["message"], "a close table name is suggested"

    dup = _catalog() + [{"name": "tbl_log", "table_key": "other.tbl_log", "metadata": {}}]
    _, issues = cs.match_tables(_schema(), dup)
    assert any(i["level"] == "error" and "ambiguous" in i["message"] for i in issues)


def test_check_reports_unknown_columns_references_and_undocumented_tables():
    schema, _ = cs.normalize_schema({"tables": {"tbl_faktor": {"columns": {
        "vaziaat": {"description": "typo"},
        "moshtari_id": {"references": "tbl_moshtari.nope"},
        "jam_kol": {"lookup": {"table": "ghost"}},
    }}}})
    report = cs.check_against_catalog(schema, _catalog())
    msgs = " | ".join(f"{i['path']}: {i['message']}" for i in report["issues"])
    assert "vaziaat: no such column" in msgs and "vaziat" in msgs      # suggestion
    assert "references" in msgs and "ghost" in msgs
    assert report["matched_tables"] == 1 and "db.tbl_kala" in report["undocumented_tables"]


# ----------------------------------------------------------------- applying

def test_user_text_wins_over_generated_and_database_comments():
    tables = _catalog()
    cs.apply_schema(tables, _schema())
    faktor, moshtari = tables[0]["metadata"], tables[1]["metadata"]
    assert faktor["description"] == "فاکتورهای فروش" and faktor["description_source"] == "schema"
    assert moshtari["description"] == "مشتریان فروشگاه", "beats the comment stored in the database"
    assert faktor["grain"] == "one row per invoice"
    cols = {c["name"]: c for c in faktor["columns"]}
    assert cols["vaziat"]["description"] == "وضعیت فاکتور"
    assert "1=تایید شده" in cols["vaziat"]["value_meanings"] and "decoded by tbl_code_paye" in cols["vaziat"]["profile"]
    assert cols["tarikh"]["semantic_type"] == "jalali_date"
    assert cols["jam_kol"]["description"] == "مبلغ کل (ریال)"
    assert faktor["relationships"][0] == "moshtari_id references tbl_moshtari.moshtari_id (declared)"
    assert "فاکتور" in faktor["keywords"] and "invoice" in faktor["keywords"]
    assert "customer" in moshtari["keywords"] and "خریدار" in moshtari["keywords"], "glossary feeds search keywords"
    assert tables[3]["metadata"]["ignored"] is True
    assert "schema_documented" not in tables[4]["metadata"], "undocumented tables are untouched"


def test_apply_is_a_no_op_without_a_schema():
    before = copy.deepcopy(_catalog())
    assert cs.apply_schema(_catalog(), None) == before
    assert cs.apply_schema(_catalog(), {"tables": {}}) == before


# --------------------------------------------- overlay, hiding, agent views

@pytest.fixture()
def stored(tmp_path):
    save_catalog(tmp_path, "mysql:x", _catalog())
    cs.save_schema(tmp_path, "mysql:x", _schema(), filename="x.yaml")
    return tmp_path


def test_schema_overlays_every_catalog_read_and_survives_a_resync(stored):
    by_key = lambda tbls: {t["table_key"]: t for t in tbls}
    assert by_key(load_catalog(stored, "mysql:x"))["db.tbl_faktor"]["metadata"]["description"] == "فاکتورهای فروش"
    save_catalog(stored, "mysql:x", _catalog())      # a re-sync writes bare metadata again
    assert by_key(load_catalog(stored, "mysql:x"))["db.tbl_faktor"]["metadata"]["description"] == "فاکتورهای فروش"
    assert cs.load_schema_record(stored, "mysql:x")["filename"] == "x.yaml"
    assert cs.delete_schema(stored, "mysql:x") and not cs.delete_schema(stored, "mysql:x")
    assert by_key(load_catalog(stored, "mysql:x"))["db.tbl_faktor"]["metadata"]["description"] == "generated guess"


def test_ignored_tables_disappear_from_the_agents_views_only(stored):
    names = [h["name"] for h in search_catalog_cache(stored, "tbl", source_ids=["mysql:x"], limit_per_source=50)]
    assert "tbl_log" not in names and "tbl_faktor" in names
    listed = [t["name"] for t in list_path_children(stored, "mysql:x", path=["db"])["tables"]]
    assert "tbl_log" not in listed and "tbl_kala" in listed
    assert list_sources_summary(stored)[0]["table_count"] == 4
    assert any(t["table_key"] == "db.tbl_log" for t in load_catalog(stored, "mysql:x")), "the UI still sees it"


def test_search_finds_tables_by_aka_glossary_value_meaning_and_grain(stored):
    def hits(q):
        return [h["table_key"] for h in search_catalog_cache(stored, q, source_ids=["mysql:x"])]
    assert "db.tbl_faktor" in hits("invoice")
    assert "db.tbl_moshtari" in hits("خریدار")
    assert "db.tbl_faktor" in hits("لغو شده")          # a status meaning written in the schema
    assert "db.tbl_faktor" in hits("per invoice")       # grain


def test_overview_is_authoritative_compact_and_capped(stored):
    tables = load_catalog(stored, "mysql:x")
    text = cs.build_overview(_schema(), tables)
    assert "Amounts are in Rials" in text and "tbl_faktor" in text and "tbl_log" not in text
    assert "مشتری" in text and "monthly" not in text.lower() or "فروش ماهانه" in text
    big = _schema()
    big["rules"] = ["rule " * 40] * 12
    assert len(cs.build_overview(big, tables, max_chars=400)) <= 430


def test_agent_summary_and_describe_use_the_schema(stored):
    from data_formulator.agents.agent_data_loading_chat import _build_connector_summary_block
    from data_formulator.agents.context import handle_read_catalog_metadata

    block = _build_connector_summary_block(stored)
    assert "USER-PROVIDED SCHEMA" in block and "Amounts are in Rials" in block and "3 tables" not in block
    ws = type("W", (), {"user_home": stored})()
    text = handle_read_catalog_metadata("mysql:x", "db.tbl_faktor", ws)
    assert "from the user's schema file" in text and "Grain (one row is): one row per invoice" in text
    assert "1=تایید شده" in text and "moshtari_id references tbl_moshtari.moshtari_id (declared)" in text


def test_analyst_context_gets_value_meanings_grain_and_rules(stored):
    from data_formulator.agents.agent_utils import build_catalog_metadata_lookups

    class Meta:
        def __init__(self, src): self.source_table = src
    ws_meta = type("M", (), {"tables": {"faktor": Meta("db.tbl_faktor")}})()
    ws = type("W", (), {"user_home": stored, "get_metadata": lambda self: ws_meta})()
    table_desc, col_desc, extra, _ = build_catalog_metadata_lookups(ws)
    assert table_desc["faktor"] == "فاکتورهای فروش"
    assert "values: 1=تایید شده" in col_desc["faktor"]["vaziat"]
    assert any("One row is: one row per invoice" in e for e in extra["faktor"])
    assert any("Rules for this database" in e and "Rials" in e for e in extra["faktor"])


# ------------------------------------------------------------------ template

def test_template_prefills_only_human_text_and_hints_the_rest():
    text = cs.build_template(_catalog(), source_label="Pakhsh")
    parsed, issues = cs.normalize_schema(cs.parse_schema_text(text, "t.yaml"))
    assert not [i for i in issues if i["level"] == "error"], issues
    assert set(parsed["tables"]) == {"tbl_faktor", "tbl_moshtari", "tbl_code_paye", "tbl_log", "tbl_kala"}
    assert parsed["tables"]["tbl_moshtari"]["description"] == "comment from the DBA"
    assert parsed["tables"]["tbl_faktor"]["description"] == "", "a generated guess must not become authoritative"
    assert "generated suggestion (check before using): generated guess" in text
    assert "~12,000 rows" in text


def test_template_round_trips_an_uploaded_schema():
    original = _schema()
    text = cs.build_template(_catalog(), existing=original)
    again, issues = cs.normalize_schema(cs.parse_schema_text(text, "t.yaml"))
    assert not [i for i in issues if i["level"] == "error"], issues
    for key in ("name", "language", "description", "rules", "glossary", "examples"):
        assert again[key] == original[key], key
    f1, f2 = original["tables"]["tbl_faktor"], again["tables"]["tbl_faktor"]
    assert f2["description"] == f1["description"] and f2["grain"] == f1["grain"] and f2["aka"] == f1["aka"]
    assert f2["columns"]["vaziat"]["values"] == f1["columns"]["vaziat"]["values"]
    assert f2["columns"]["vaziat"]["lookup"] == f1["columns"]["vaziat"]["lookup"]
    assert f2["columns"]["moshtari_id"]["references"] == "tbl_moshtari.moshtari_id"
    assert again["tables"]["tbl_moshtari"]["description"] == "مشتریان فروشگاه"
    assert again["tables"]["tbl_log"]["ignore"] is True


# ------------------------------------------------- tolerant table-key resolution

def test_short_and_case_variant_keys_resolve_to_the_real_table():
    from data_formulator.datalake.catalog_cache import resolve_catalog_entry

    cat = _catalog()
    for key in ("db.tbl_faktor", "tbl_faktor", "TBL_FAKTOR", "db.TBL_faktor"):
        entry, _ = resolve_catalog_entry(cat, key)
        assert entry is not None and entry["table_key"] == "db.tbl_faktor", key
    entry, hints = resolve_catalog_entry(cat, "tbl_fakter")
    assert entry is None and "db.tbl_faktor" in hints, "a typo gets a suggestion, not a guess"
    dup = cat + [{"name": "tbl_log", "table_key": "other.tbl_log", "metadata": {}}]
    entry, hints = resolve_catalog_entry(dup, "tbl_log")
    assert entry is None and set(hints) == {"db.tbl_log", "other.tbl_log"}, "ambiguous names are not guessed"


def test_describe_and_load_plan_accept_short_names(stored):
    from data_formulator.agents.agent_data_loading_chat import DataLoadingAgent
    from data_formulator.agents.context import handle_read_catalog_metadata

    ws = type("W", (), {"user_home": stored})()
    assert "Table key: db.tbl_faktor" in handle_read_catalog_metadata("mysql:x", "tbl_faktor", ws)
    missing = handle_read_catalog_metadata("mysql:x", "tbl_fakter", ws)
    assert "not found" in missing and "Did you mean: db.tbl_faktor" in missing

    agent = DataLoadingAgent.__new__(DataLoadingAgent)
    agent.workspace = ws
    agent._known_source_ids = lambda: {"mysql:x"}
    cand = agent._normalize_load_plan_candidate({"source_id": "mysql:x", "table_key": "tbl_moshtari"})
    assert "resolution_error" not in cand and cand["table_key"] == "db.tbl_moshtari"
    assert agent._resolve_catalog_path("mysql:x", "tbl_moshtari") == ["db", "tbl_moshtari"]


def test_overview_shows_full_keys_in_glossary_and_examples(stored):
    text = cs.build_overview(_schema(), load_catalog(stored, "mysql:x"))
    assert "→ db.tbl_moshtari" in text and "→ db.tbl_faktor" in text
    assert "use these exact table_key values" in text
