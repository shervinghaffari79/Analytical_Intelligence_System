"""Automatic metadata for undocumented sources (profiling + overlay)."""
from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pytest

from data_formulator.datalake import catalog_profile as cp
from data_formulator.datalake.catalog_cache import save_catalog, search_catalog_cache

pytestmark = [pytest.mark.backend]


class _Loader:
    """Serves fixed frames; mimics the loader surface the profiler uses."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def fetch_data_as_arrow(self, source_table, import_options=None):
        df = self.frames[source_table]
        return pa.Table.from_pandas(df.head((import_options or {}).get("size", len(df))), preserve_index=False)

    def probe(self, path, query):
        raise NotImplementedError


def _table(key: str, df: pd.DataFrame, pk: str | None = None) -> dict:
    cols = [{"name": c, "type": str(df[c].dtype)} for c in df.columns]
    for c in cols:
        if c["name"] == pk:
            c["key"] = "PRI"
    return {"name": key, "table_key": key, "path": key.split("."),
            "metadata": {"_source_name": key, "columns": cols}}


@pytest.fixture()
def source():
    cust = pd.DataFrame({
        "moshtari_id": range(1, 201),
        "famil": ["کریمی", "كريمي", "احمدی", "رضایی"] * 50,
        "mobile": ["09121234567", "۰۹۳۵۱۲۳۴۵۶۷"] * 100,
        "tarikh_ozviat": [f"1402/{1 + i % 12:02d}/{1 + i % 28:02d}" for i in range(200)],
    })
    inv = pd.DataFrame({
        "faktor_id": range(1, 601),
        "moshtari_id": [1 + i % 200 for i in range(600)],
        "tarikh": [14020101 + (i % 28) for i in range(600)],
        "vaziat": [1 + i % 3 for i in range(600)],
        "jam_kol": [1000 * (i + 1) for i in range(600)],
    })
    opaque = pd.DataFrame({"f1": range(1, 301), "f2": [1 + (i * 7) % 200 for i in range(300)]})
    codes = pd.DataFrame({"noe": ["vaziat_faktor"] * 3, "code": [1, 2, 3],
                          "sharh": ["تایید شده", "لغو شده", "مرجوع شده"]})
    frames = {"db.tbl_moshtari": cust, "db.tbl_faktor": inv, "db.t_x": opaque, "db.tbl_code": codes}
    tables = [_table("db.tbl_moshtari", cust, "moshtari_id"), _table("db.tbl_faktor", inv, "faktor_id"),
              _table("db.t_x", opaque, "f1"), _table("db.tbl_code", codes)]
    return _Loader(frames), tables


def test_profile_detects_persian_specific_semantics(source):
    loader, tables = source
    store: dict = {}
    summary = cp.build_profiles(loader, tables, store)
    assert summary["profiled"] == 4 and summary["failed"] == 0

    cust = store["tables"]["db.tbl_moshtari"]["profile"]["columns"]
    assert cust["tarikh_ozviat"]["semantic_type"] == "jalali_date"
    assert cust["tarikh_ozviat"]["min"].startswith("1402/01")
    assert cust["mobile"]["semantic_type"] == "iran_mobile"
    assert cust["mobile"]["persian_digits"] is True
    assert cust["famil"]["mixed_letter_forms"] is True

    inv = store["tables"]["db.tbl_faktor"]["profile"]["columns"]
    assert inv["tarikh"]["semantic_type"] == "jalali_date_int"
    assert inv["vaziat"]["semantic_type"] == "code"
    assert "semantic_type" not in inv["jam_kol"]


def test_relationships_inferred_without_declared_foreign_keys(source):
    loader, tables = source
    store: dict = {}
    cp.build_profiles(loader, tables, store)
    rel = {(r["column"], r["ref_table"], r["confidence"])
           for r in store["tables"]["db.tbl_faktor"]["relationships"]}
    assert ("moshtari_id", "db.tbl_moshtari", "high") in rel
    # Opaque column name: found by value range, reported with low confidence.
    assert store["tables"]["db.t_x"]["relationships"] == [
        {"column": "f2", "ref_table": "db.tbl_moshtari", "ref_column": "moshtari_id", "confidence": "low"}]
    # A date or amount column is never mistaken for a reference.
    assert not any(c in ("tarikh", "jam_kol") for c, _, _ in rel)
    assert any("tbl_code" in h and "vaziat_faktor" in h
               for h in store["tables"]["db.tbl_faktor"]["lookup_hints"])


def test_apply_profiles_never_overrides_source_descriptions(source):
    loader, tables = source
    store: dict = {}
    cp.build_profiles(loader, tables, store)
    store["tables"]["db.tbl_faktor"]["description"] = {
        "table": "generated table text", "keywords": ["invoice", "فاکتور"],
        "columns": {"vaziat": "generated", "jam_kol": "generated total"}}
    inv = tables[1]
    inv["metadata"]["columns"][3]["description"] = "from the source"  # vaziat
    cp.apply_profiles(tables, store)
    meta = inv["metadata"]
    assert meta["description"] == "generated table text" and meta["description_source"] == "auto"
    by = {c["name"]: c for c in meta["columns"]}
    assert by["vaziat"]["description"] == "from the source"
    assert by["jam_kol"]["description"] == "generated total"
    assert "Jalali" in by["tarikh"]["profile"]
    assert any("tbl_moshtari.moshtari_id" in r for r in meta["relationships"])

    meta["description"], meta["description_source"] = "DBA comment", "source"
    cp.apply_profiles(tables, store)
    assert meta["description"] == "DBA comment"


def test_overlay_survives_resync_and_feeds_search(tmp_path, source):
    loader, tables = source
    store = cp.load_profiles(tmp_path, "mysql:x")
    cp.build_profiles(loader, tables, store)
    store["tables"]["db.tbl_faktor"]["description"] = {
        "table": "Sales invoices", "keywords": ["invoice", "فاکتور", "فروش"], "columns": {}}
    cp.save_profiles(tmp_path, "mysql:x", store)

    fresh = [_table(t["table_key"], loader.frames[t["table_key"]]) for t in tables]
    save_catalog(tmp_path, "mysql:x", fresh)  # a re-sync writes bare metadata

    def keys(query):
        return [h["table_key"] for h in search_catalog_cache(tmp_path, query, source_ids=["mysql:x"])]

    assert keys("invoice")[0] == "db.tbl_faktor"
    assert "db.tbl_faktor" in keys("فروش")
    # Value search, insensitive to Arabic/Persian letter forms.
    assert "db.tbl_moshtari" in keys("كريمي") and "db.tbl_moshtari" in keys("کریمی")
    assert "db.tbl_code" in keys("لغو")


def test_describe_agent_parses_and_filters_model_output(source):
    from data_formulator.agents.agent_catalog_describe import CatalogDescribeAgent

    loader, tables = source
    store: dict = {}
    cp.build_profiles(loader, tables, store)
    cp.apply_profiles(tables, store)

    class _Msg:
        content = ('Sure.\n```json\n{"table": "Sales invoices.", "table_local": "فاکتورهای فروش", '
                   '"keywords": ["invoice", "فاکتور", "invoice"], '
                   '"columns": {"vaziat": "invoice status code", "not_a_column": "x"}}\n```')

    class _Client:
        def __init__(self):
            self.prompt = None

        def get_completion(self, messages, **kw):
            self.prompt = messages[-1]["content"]
            return type("R", (), {"choices": [type("C", (), {"message": _Msg()})()]})()

    client = _Client()
    summary = cp.describe_tables(CatalogDescribeAgent(client), tables, store, only={"db.tbl_faktor"})
    assert summary["described"] == 1
    d = store["tables"]["db.tbl_faktor"]["description"]
    assert d["table"] == "Sales invoices. — فاکتورهای فروش"
    assert d["keywords"] == ["invoice", "فاکتور"]
    assert d["columns"] == {"vaziat": "invoice status code"}
    # The prompt carries measured facts and inferred relationships.
    assert "Jalali" in client.prompt and "references tbl_moshtari.moshtari_id" in client.prompt
