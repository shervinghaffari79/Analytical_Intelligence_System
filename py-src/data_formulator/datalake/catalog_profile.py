# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Automatic metadata for sources that ship none.

Many production databases have no table or column comments, no declared
foreign keys, and identifiers that are abbreviations or transliterations
(``tbl_faktor_riz.fi``). The agents then have nothing to search or reason over
but opaque names. This module derives metadata from the data itself, following
the profiling approach of Shkapenyuk et al., "Automatic Metadata Extraction for
Text-to-SQL" (arXiv:2505.19988) and the schema presentation of M-Schema
(XiYan-SQL, arXiv:2411.08599):

1. **Profile** a bounded sample of every table: null rate, cardinality, value
   range, frequent values, and a semantic type inferred from value shape
   (Jalali date, mobile number, low-cardinality code, ...).
2. **Infer relationships** between tables that declare none, from matching
   column names and value-range containment, and spot lookup tables whose
   *values* name another table's column.
3. Optionally have an LLM turn each profile into a short bilingual description
   and search keywords (see ``agents/agent_catalog_describe.py``).

Results are stored per source next to the catalog cache and overlaid on catalog
entries by :func:`apply_profiles`, so a catalog re-sync never discards them and
source-provided descriptions always win over generated ones.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from data_formulator.data_loader.text_folding import fold_text, has_arabic_script

logger = logging.getLogger(__name__)

PROFILE_VERSION = 1
SAMPLE_ROWS = 2000
MAX_EXAMPLES = 5
ENUM_MAX_DISTINCT = 12
_EXAMPLE_CHARS = 40

_JALALI_STR_RE = re.compile(r"^1[34]\d\d[/\-.](0?[1-9]|1[0-2])[/\-.](0?[1-9]|[12]\d|3[01])$")
_GREGORIAN_STR_RE = re.compile(r"^(19|20)\d\d[/\-.](0?[1-9]|1[0-2])[/\-.](0?[1-9]|[12]\d|3[01])")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_MOBILE_RE = re.compile(r"^(\+98|0098|0)?9\d{9}$")
_DIGITS_RE = re.compile(r"^\d+$")
_ARABIC_VARIANT_RE = re.compile(r"[يىك]")
_NON_ASCII_DIGIT_RE = re.compile(r"[٠-٩۰-۹]")

# Semantic type -> note shown to the agent. Only facts that change how a column
# must be queried belong here.
SEMANTIC_NOTES: dict[str, str] = {
    "jalali_date": (
        "Jalali (Solar Hijri) date stored as text YYYY/MM/DD — filter with Jalali "
        "strings in the same format (e.g. 1402/01/01), never Gregorian dates; "
        "string comparison orders correctly"
    ),
    "jalali_date_int": (
        "Jalali (Solar Hijri) date stored as an integer YYYYMMDD (e.g. 14020101) "
        "— filter with integers in that form, never Gregorian dates"
    ),
    "jalali_year": "Jalali (Solar Hijri) year, e.g. 1402",
    "gregorian_date": "Gregorian date stored as text",
    "time": "time of day stored as text HH:MM",
    "iran_mobile": "Iranian mobile number; digits may be Persian or ASCII",
    "national_id": "10-digit Iranian national ID",
    "flag": "boolean flag stored as 0/1",
    "code": "numeric code (enumeration); meanings may live in a lookup table",
    "category": "categorical text",
}


# ---------------------------------------------------------------------------
# Column / table profiling
# ---------------------------------------------------------------------------

def _short(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _EXAMPLE_CHARS:
        return v[:_EXAMPLE_CHARS] + "…"
    return v


def _plain(v: Any) -> Any:
    """Make a value JSON-serialisable."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    try:
        import numpy as np  # local import: optional fast path
        if isinstance(v, np.generic):
            return v.item()
    except Exception:
        pass
    return str(v)


def _semantic_type(name: str, values: list[Any], distinct: int, is_unique: bool) -> str | None:
    """Infer a semantic type from value shape. ``values`` are non-null."""
    if not values:
        return None
    n = len(values)
    sample = values[:500]

    if all(isinstance(v, bool) for v in sample):
        return "flag"

    if all(isinstance(v, int) and not isinstance(v, bool) for v in sample):
        lo, hi = min(values), max(values)
        if 13000101 <= lo and hi <= 14991231 and all(
            1 <= (v // 100) % 100 <= 12 and 1 <= v % 100 <= 31 for v in sample
        ):
            return "jalali_date_int"
        if 1300 <= lo and hi <= 1499 and distinct <= 100:
            return "jalali_year"
        if distinct <= 2 and lo >= 0 and hi <= 1 and not is_unique:
            return "flag"
        if distinct <= ENUM_MAX_DISTINCT and n >= 3 * distinct and not is_unique and 0 <= lo and hi <= 99:
            return "code"
        return None

    if all(isinstance(v, str) for v in sample):
        folded = [fold_text(v) for v in sample if v.strip()]
        if not folded:
            return None

        def frac(rx: re.Pattern) -> float:
            return sum(1 for v in folded if rx.match(v)) / len(folded)

        if frac(_JALALI_STR_RE) >= 0.95:
            return "jalali_date"
        if frac(_GREGORIAN_STR_RE) >= 0.95:
            return "gregorian_date"
        if frac(_TIME_RE) >= 0.95:
            return "time"
        if frac(_MOBILE_RE) >= 0.9:
            return "iran_mobile"
        if frac(_DIGITS_RE) >= 0.95 and all(len(v) == 10 for v in folded[:200]) and distinct > ENUM_MAX_DISTINCT:
            return "national_id"
        if distinct <= ENUM_MAX_DISTINCT and n >= 3 * distinct and not is_unique:
            return "category"
    return None


def profile_dataframe(df: Any, *, row_count: int | None = None) -> dict[str, Any]:
    """Profile a sample ``DataFrame``. Returns a JSON-serialisable dict."""
    n_rows = int(len(df))
    cols: dict[str, Any] = {}
    for name in df.columns:
        series = df[name]
        non_null = [v for v in (_plain(x) for x in series.tolist()) if v is not None and v == v]
        n = len(non_null)
        info: dict[str, Any] = {
            "null_frac": round(1 - n / n_rows, 3) if n_rows else 0.0,
        }
        if not n:
            info["distinct"] = 0
            cols[str(name)] = info
            continue

        hashable = [v if isinstance(v, (int, float, str, bool)) else str(v) for v in non_null]
        counts = Counter(hashable)
        distinct = len(counts)
        is_unique = distinct == n and n == n_rows and n_rows > 1
        info["distinct"] = distinct
        if is_unique:
            info["unique"] = True

        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in hashable)
        if numeric:
            info["min"], info["max"] = min(hashable), max(hashable)
        else:
            strs = [v for v in hashable if isinstance(v, str)]
            if strs:
                info["max_len"] = max(len(v) for v in strs)
                if any(has_arabic_script(v) for v in strs[:300]):
                    info["script"] = "persian/arabic"
                    if any(_ARABIC_VARIANT_RE.search(v) for v in strs):
                        info["mixed_letter_forms"] = True
                if any(_NON_ASCII_DIGIT_RE.search(v) for v in strs):
                    info["persian_digits"] = True

        sem = _semantic_type(str(name), hashable, distinct, is_unique)
        if sem:
            info["semantic_type"] = sem
            if sem in ("jalali_date", "gregorian_date"):
                folded = sorted(fold_text(v) for v in hashable if isinstance(v, str))
                info["min"], info["max"] = folded[0], folded[-1]

        top = counts.most_common(ENUM_MAX_DISTINCT if distinct <= ENUM_MAX_DISTINCT else MAX_EXAMPLES)
        if distinct <= ENUM_MAX_DISTINCT and not is_unique:
            info["values"] = [[_short(v), c] for v, c in top]
        else:
            info["examples"] = [_short(v) for v, _ in top]
        cols[str(name)] = info

    out: dict[str, Any] = {"sampled_rows": n_rows, "columns": cols}
    if row_count is not None:
        out["row_count"] = int(row_count)
    return out


def _source_table_name(table: dict[str, Any]) -> str:
    meta = table.get("metadata") or {}
    return meta.get("_source_name") or table.get("table_key") or table.get("name", "")


def profile_table(loader: Any, table: dict[str, Any], *, sample_rows: int = SAMPLE_ROWS) -> dict[str, Any]:
    """Profile one catalog table through its live loader (bounded reads only)."""
    source_table = _source_table_name(table)
    arrow = loader.fetch_data_as_arrow(source_table=source_table, import_options={"size": sample_rows})
    df = arrow.to_pandas()

    row_count: int | None = None
    exact: dict[str, dict[str, Any]] = {}
    path = table.get("path")
    if path and len(df) >= sample_rows:
        # The sample is the first N rows; get the true size and the true range
        # of integer columns, which relationship inference depends on.
        int_cols = [c for c in df.columns if str(df[c].dtype).startswith(("int", "Int", "uint"))][:10]
        aggs: list[dict[str, Any]] = [{"op": "count", "as": "_n"}]
        for i, c in enumerate(int_cols):
            aggs += [{"op": "min", "column": c, "as": f"_lo{i}"}, {"op": "max", "column": c, "as": f"_hi{i}"}]
        try:
            res = loader.probe(list(path), {"aggregates": aggs})
            row = (res.get("rows") or [{}])[0] if isinstance(res, dict) else {}
            if row.get("_n") is not None:
                row_count = int(row["_n"])
            for i, c in enumerate(int_cols):
                if row.get(f"_lo{i}") is not None:
                    exact[str(c)] = {"min": int(row[f"_lo{i}"]), "max": int(row[f"_hi{i}"])}
        except Exception:
            logger.debug("profile_table: exact stats failed for %s", source_table, exc_info=True)
    elif len(df) < sample_rows:
        row_count = len(df)

    prof = profile_dataframe(df, row_count=row_count)
    for c, rng in exact.items():
        if c in prof["columns"]:
            prof["columns"][c].update(rng)

    # Declared keys, when the loader reports them.
    for col in (table.get("metadata") or {}).get("columns", []):
        key = col.get("key")
        if key and col.get("name") in prof["columns"]:
            prof["columns"][col["name"]]["key"] = key
    return prof


# ---------------------------------------------------------------------------
# Relationship inference
# ---------------------------------------------------------------------------

def _short_name(table_key: str) -> str:
    return table_key.split(".")[-1]


def _key_columns(profile: dict[str, Any]) -> list[str]:
    """Columns that can be the target of a reference: single-column keys."""
    cols = profile.get("columns", {})
    declared = [c for c, i in cols.items() if i.get("key") == "PRI"]
    if len(declared) == 1:
        return declared
    if len(declared) > 1:
        return []  # composite key: no single-column target
    return [c for c, i in cols.items() if i.get("unique") and "min" in i and isinstance(i.get("min"), int)]


def infer_relationships(profiles: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Infer likely foreign keys between profiled tables.

    * ``high`` confidence — a column shares its name with another table's key
      column and its values fall inside that key's range.
    * ``low`` confidence — names differ, but the column's integer range tightly
      matches a key range of a reasonably large table (covers opaque names
      such as ``f2``). Reported as "possible".
    """
    keys: list[tuple[str, str, dict[str, Any]]] = []
    for tk, prof in profiles.items():
        for kc in _key_columns(prof):
            keys.append((tk, kc, prof["columns"][kc]))

    out: dict[str, list[dict[str, Any]]] = {}
    for tk, prof in profiles.items():
        own_keys = set(_key_columns(prof))
        for cname, info in prof.get("columns", {}).items():
            if cname in own_keys or not isinstance(info.get("min"), int) or not isinstance(info.get("max"), int):
                continue
            if info.get("semantic_type") in ("jalali_date_int", "jalali_year", "flag"):
                continue
            lo, hi = info["min"], info["max"]
            best: dict[str, Any] | None = None
            for ktable, kcol, kinfo in keys:
                if ktable == tk or not isinstance(kinfo.get("min"), int):
                    continue
                klo, khi = kinfo["min"], kinfo["max"]
                if lo < klo or hi > khi:
                    continue
                same_name = cname.lower() == kcol.lower()
                span = max(khi - klo + 1, 1)
                tightness = (hi - lo + 1) / span
                if same_name:
                    cand = {"column": cname, "ref_table": ktable, "ref_column": kcol,
                            "confidence": "high", "_score": 2 + tightness}
                elif (span >= 20 and tightness >= 0.9 and (lo - klo) <= 0.02 * span
                      and info.get("distinct", 0) > ENUM_MAX_DISTINCT
                      and info.get("semantic_type") is None):
                    cand = {"column": cname, "ref_table": ktable, "ref_column": kcol,
                            "confidence": "low", "_score": tightness}
                else:
                    continue
                if best is None or cand["_score"] > best["_score"]:
                    best = cand
            if best:
                best.pop("_score", None)
                out.setdefault(tk, []).append(best)
    return out


def infer_lookup_hints(profiles: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Find lookup tables whose *values* name another table's column.

    Legacy schemas often keep every enumeration in one generic table
    (``kind, code, label``). If some table's text column contains the value
    ``vaziat_faktor`` and ``tbl_faktor`` has a column ``vaziat``, the generic
    table very likely decodes it.
    """
    value_index: dict[str, list[tuple[str, str]]] = {}
    for tk, prof in profiles.items():
        for cname, info in prof.get("columns", {}).items():
            for v in [x[0] for x in info.get("values", [])] + list(info.get("examples", [])):
                if isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,40}", v):
                    value_index.setdefault(v.lower(), []).append((tk, cname))

    hints: dict[str, list[str]] = {}
    for tk, prof in profiles.items():
        stem = re.sub(r"^(tbl|tb|t)_", "", _short_name(tk).lower())
        for cname, info in prof.get("columns", {}).items():
            if info.get("semantic_type") not in ("code", "category", "flag") and info.get("distinct", 99) > ENUM_MAX_DISTINCT:
                continue
            stem_tokens = [tok for tok in stem.split("_") if tok]
            cands = [f"{cname}_{stem}".lower()]
            cands += [f"{cname}_{tok}".lower() for tok in stem_tokens]
            cands.append(cname.lower())
            hit = next(
                ((cand, ltable, lcol) for cand in cands
                 for ltable, lcol in value_index.get(cand, []) if ltable != tk),
                None,
            )
            if hit:
                cand, ltable, lcol = hit
                hints.setdefault(tk, []).append(
                    f"{cname}: codes are probably decoded by {_short_name(ltable)} "
                    f"(rows where {lcol} = '{cand}')"
                )
    return hints


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_path(user_home: Path | str, source_id: str, *, mkdir: bool = False) -> Path:
    from data_formulator.datalake.catalog_cache import _cache_filename
    d = Path(user_home) / "catalog_profile"
    if mkdir:
        d.mkdir(parents=True, exist_ok=True)
    return d / _cache_filename(source_id)


def load_profiles(user_home: Path | str, source_id: str) -> dict[str, Any]:
    """Return ``{"tables": {table_key: entry}}`` (empty when nothing stored)."""
    path = _store_path(user_home, source_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and raw.get("version") == PROFILE_VERSION:
            return raw
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Failed to read catalog profile %s", path, exc_info=True)
    return {"version": PROFILE_VERSION, "source_id": source_id, "tables": {}}


def save_profiles(user_home: Path | str, source_id: str, store: dict[str, Any]) -> None:
    path = _store_path(user_home, source_id, mkdir=True)
    store["version"] = PROFILE_VERSION
    store["source_id"] = source_id
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Building profiles for a source
# ---------------------------------------------------------------------------

def build_profiles(
    loader: Any,
    tables: list[dict[str, Any]],
    store: dict[str, Any],
    *,
    only: set[str] | None = None,
    refresh: bool = False,
    time_budget_s: float = 120.0,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, int]:
    """Profile catalog tables into *store* (mutated). Returns a summary."""
    started = time.monotonic()
    entries = store.setdefault("tables", {})
    todo = [t for t in tables if t.get("table_key") and (only is None or t["table_key"] in only)]
    summary = {"profiled": 0, "skipped": 0, "failed": 0, "remaining": 0, "total": len(todo)}
    for i, t in enumerate(todo):
        tk = t["table_key"]
        if not refresh and "profile" in entries.get(tk, {}):
            summary["skipped"] += 1
            continue
        if time.monotonic() - started > time_budget_s:
            summary["remaining"] = len(todo) - i
            break
        if on_progress:
            on_progress(i, len(todo), tk)
        try:
            entries.setdefault(tk, {})["profile"] = profile_table(loader, t)
            entries[tk]["profiled_at"] = int(time.time())
            summary["profiled"] += 1
        except Exception as exc:
            logger.warning("Profiling failed for %s: %s", tk, type(exc).__name__)
            logger.debug("Profiling failure detail", exc_info=True)
            summary["failed"] += 1

    profiles = {tk: e["profile"] for tk, e in entries.items() if "profile" in e}
    rels = infer_relationships(profiles)
    hints = infer_lookup_hints(profiles)
    for tk, e in entries.items():
        e["relationships"] = rels.get(tk, [])
        e["lookup_hints"] = hints.get(tk, [])
    return summary


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def column_facts(info: dict[str, Any], *, is_reference: bool = False) -> str:
    """One-line, model-facing summary of a column profile.

    ``is_reference`` marks a column already explained by an inferred
    relationship, so the generic "numeric code" note is not repeated for it.
    """
    parts: list[str] = []
    if info.get("key") == "PRI":
        parts.append("primary key")
    elif info.get("unique"):
        parts.append("unique")
    sem = info.get("semantic_type")
    if sem == "code" and (is_reference or info.get("key") == "PRI"):
        sem = None
    if sem in SEMANTIC_NOTES:
        parts.append(SEMANTIC_NOTES[sem])
    if "min" in info and "max" in info and info["min"] != info["max"]:
        parts.append(f"range {info['min']}..{info['max']}")
    if info.get("values"):
        vals = ", ".join(f"{v}" for v, _ in info["values"])
        parts.append(f"values: {vals}")
    elif info.get("examples"):
        vals = ", ".join(f"{v}" for v in info["examples"][:MAX_EXAMPLES])
        parts.append(f"e.g. {vals}")
    if info.get("mixed_letter_forms"):
        parts.append("mixes Arabic and Persian letter forms (ي/ی, ك/ک) — text filters match both")
    if info.get("persian_digits"):
        parts.append("some values use Persian digits")
    if info.get("null_frac", 0) >= 0.5:
        parts.append(f"{int(info['null_frac'] * 100)}% null")
    return "; ".join(parts)


def relationship_lines(entry: dict[str, Any]) -> list[str]:
    lines = []
    for r in entry.get("relationships", []):
        prefix = "" if r.get("confidence") == "high" else "possibly "
        lines.append(f"{r['column']} {prefix}references {_short_name(r['ref_table'])}.{r['ref_column']}")
    lines.extend(entry.get("lookup_hints", []))
    return lines


def apply_profiles(tables: list[dict[str, Any]], store: dict[str, Any]) -> list[dict[str, Any]]:
    """Overlay stored profiles and generated descriptions on catalog *tables*.

    Mutates and returns *tables*. Source-provided descriptions are never
    replaced; generated text is marked with ``description_source = "auto"``.
    """
    entries = (store or {}).get("tables", {})
    if not entries:
        return tables
    for t in tables:
        e = entries.get(t.get("table_key", ""))
        if not e:
            continue
        meta = t.setdefault("metadata", {})
        prof = e.get("profile") or {}
        desc = e.get("description") or {}

        if prof.get("row_count") is not None and not meta.get("row_count"):
            meta["row_count"] = prof["row_count"]
        if desc.get("table") and not meta.get("description"):
            meta["description"] = desc["table"]
            meta["description_source"] = "auto"
        if desc.get("keywords"):
            meta["keywords"] = desc["keywords"]
        rel = relationship_lines(e)
        if rel:
            meta["relationships"] = rel

        col_desc = desc.get("columns") or {}
        col_prof = prof.get("columns") or {}
        ref_cols = {r["column"] for r in e.get("relationships", [])}
        for col in meta.get("columns", []):
            name = col.get("name")
            info = col_prof.get(name)
            if info:
                facts = column_facts(info, is_reference=name in ref_cols)
                if facts:
                    col["profile"] = facts
                if info.get("semantic_type"):
                    col["semantic_type"] = info["semantic_type"]
            if col_desc.get(name) and not col.get("description"):
                col["description"] = col_desc[name]
                col["description_source"] = "auto"
    return tables


def describe_tables(
    agent: Any,
    tables: list[dict[str, Any]],
    store: dict[str, Any],
    *,
    only: set[str] | None = None,
    refresh: bool = False,
    time_budget_s: float = 600.0,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, int]:
    """Generate descriptions for profiled tables that have none.

    *agent* is a ``CatalogDescribeAgent``. Tables that already carry a
    source-provided description for the table and every column are skipped.
    """
    started = time.monotonic()
    entries = store.setdefault("tables", {})
    names = [_short_name(t.get("table_key", "")) for t in tables if t.get("table_key")]
    todo = [t for t in tables if t.get("table_key") in entries
            and "profile" in entries[t["table_key"]]
            and (only is None or t["table_key"] in only)]
    summary = {"described": 0, "skipped": 0, "failed": 0, "remaining": 0, "total": len(todo)}
    for i, t in enumerate(todo):
        tk = t["table_key"]
        e = entries[tk]
        meta = t.get("metadata") or {}
        documented = (
            meta.get("description") and meta.get("description_source") != "auto"
            and all(c.get("description") and c.get("description_source") != "auto"
                    for c in meta.get("columns", []))
        )
        if documented or (e.get("description") and not refresh):
            summary["skipped"] += 1
            continue
        if time.monotonic() - started > time_budget_s:
            summary["remaining"] = len(todo) - i
            break
        if on_progress:
            on_progress(i, len(todo), tk)
        result = agent.describe(t, e, [n for n in names if n != _short_name(tk)])
        if result:
            e["description"] = result
            e["described_at"] = int(time.time())
            summary["described"] += 1
        else:
            summary["failed"] += 1
    return summary
