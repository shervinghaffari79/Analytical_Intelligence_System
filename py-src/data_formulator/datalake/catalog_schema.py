# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""User-provided schema files for databases the agents cannot understand alone.

Crawling a database (profiling samples, inferring keys, asking a model to describe
every table) works, but it is slow, approximate, and cannot know what only a person
knows: that ``vaziat = 3`` means "cancelled", that amounts are in Rials, that
``t_prs01`` is the sales-visit log, that ``tbl_log`` is noise. A schema file lets
that person say so once. The system then validates it against the live catalog,
stores it beside the catalog cache, and overlays it on every catalog read, so the
agents see it in ``find_data``, ``describe_data``, the connected-sources summary and
the analyst's table context — without crawling.

Formats
-------
* **YAML / JSON** — the full format (see ``docs/schema-file.md``).
* **CSV data dictionary** — one row per column: ``table, column, description`` and
  optionally ``type, unit, values, references, table_description``. The shape most
  organisations already keep in a spreadsheet.

Anything the file does not say falls back to the generated metadata; anything it
does say wins, including over comments stored in the database itself.
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_SCHEMA_BYTES = 2_000_000
MAX_OVERVIEW_CHARS = 3500

# ``type`` values that change how a column must be queried; they reuse the profile's
# semantic types so the analyst gets the same warnings (e.g. Jalali dates).
KNOWN_TYPES = {
    "jalali_date", "jalali_date_int", "jalali_year", "gregorian_date", "time",
    "iran_mobile", "national_id", "flag", "code", "category",
    "id", "money", "quantity", "percent", "text", "number", "date",
}
_SEMANTIC_TYPES = {
    "jalali_date", "jalali_date_int", "jalali_year", "gregorian_date", "time",
    "iran_mobile", "national_id", "flag", "code", "category",
}


class SchemaFileError(ValueError):
    """The file cannot be read at all (not YAML/JSON/CSV, wrong shape, too big)."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_schema_text(text: str, filename: str = "") -> dict[str, Any]:
    """Return the raw mapping described by *text* (format chosen from *filename*)."""
    if len(text.encode("utf-8", "ignore")) > MAX_SCHEMA_BYTES:
        raise SchemaFileError(f"The schema file is larger than {MAX_SCHEMA_BYTES // 1_000_000} MB.")
    text = text.lstrip("﻿")
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv" or (not ext and _looks_like_csv(text)):
        return _parse_csv(text)
    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaFileError(f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
    else:
        try:
            import yaml
            data = yaml.safe_load(text)
        except Exception as exc:  # yaml.YAMLError and friends
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
            problem = getattr(exc, "problem", None) or str(exc)
            raise SchemaFileError(f"Invalid YAML: {problem}{where}.") from exc
    if not isinstance(data, dict):
        raise SchemaFileError("The schema file must be a mapping with a `tables:` section.")
    return data


def _looks_like_csv(text: str) -> bool:
    first = text.strip().splitlines()[0].lower() if text.strip() else ""
    return "table" in first and "column" in first and "," in first and ":" not in first


def _parse_csv(text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SchemaFileError("The CSV is empty.")
    cols = {(c or "").strip().lower().replace(" ", "_"): c for c in reader.fieldnames}
    if "table" not in cols or "column" not in cols:
        raise SchemaFileError("The CSV needs at least the columns `table` and `column` (plus `description`).")

    def cell(row: dict, key: str) -> str:
        return (row.get(cols[key]) or "").strip() if key in cols else ""

    tables: dict[str, Any] = {}
    for row in reader:
        t, c = cell(row, "table"), cell(row, "column")
        if not t:
            continue
        entry = tables.setdefault(t, {"columns": {}})
        td = cell(row, "table_description")
        if td and not entry.get("description"):
            entry["description"] = td
        if not c:
            continue
        col: dict[str, Any] = {}
        for src, dst in (("description", "description"), ("type", "type"), ("unit", "unit"), ("references", "references")):
            if cell(row, src):
                col[dst] = cell(row, src)
        vals = cell(row, "values")
        if vals:
            col["values"] = _parse_values_cell(vals)
        entry["columns"][c] = col
    return {"version": SCHEMA_VERSION, "tables": tables}


def _parse_values_cell(text: str) -> dict[str, str]:
    """``1=تایید شده; 2=لغو شده`` (also ``|`` or newline separated) -> mapping."""
    out: dict[str, str] = {}
    for part in re.split(r"[;|\n]+", text):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Normalisation + validation of the file itself
# ---------------------------------------------------------------------------

def _issue(level: str, path: str, message: str) -> dict[str, str]:
    return {"level": level, "path": path, "message": message}


def _str_list(value: Any, path: str, issues: list) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    issues.append(_issue("error", path, "must be a text or a list of texts"))
    return []


def _text(value: Any, path: str, issues: list) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    issues.append(_issue("error", path, "must be text"))
    return ""


def normalize_schema(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate the file's own structure. Returns ``(schema, issues)``.

    Errors make the schema unusable; warnings (unknown keys, odd types) do not.
    """
    issues: list[dict[str, str]] = []
    top_known = {"version", "name", "language", "description", "rules", "glossary", "examples", "tables", "source"}
    for k in raw:
        if k not in top_known:
            issues.append(_issue("warning", k, "unknown key (ignored)"))

    version = raw.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        issues.append(_issue("warning", "version", f"expected {SCHEMA_VERSION}, got {version!r}; reading it as version {SCHEMA_VERSION}"))

    schema: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "name": _text(raw.get("name"), "name", issues),
        "language": _text(raw.get("language"), "language", issues),
        "description": _text(raw.get("description"), "description", issues),
        "rules": _str_list(raw.get("rules"), "rules", issues),
        "glossary": [],
        "examples": [],
        "tables": {},
    }

    for i, g in enumerate(raw.get("glossary") or []):
        p = f"glossary[{i}]"
        if not isinstance(g, dict) or not g.get("term"):
            issues.append(_issue("error", p, "each glossary entry needs a `term`"))
            continue
        schema["glossary"].append({
            "term": _text(g.get("term"), p + ".term", issues),
            "aka": _str_list(g.get("aka"), p + ".aka", issues),
            "meaning": _text(g.get("meaning"), p + ".meaning", issues),
            "maps_to": _str_list(g.get("maps_to"), p + ".maps_to", issues),
        })

    for i, e in enumerate(raw.get("examples") or []):
        p = f"examples[{i}]"
        if not isinstance(e, dict) or not e.get("question"):
            issues.append(_issue("error", p, "each example needs a `question`"))
            continue
        schema["examples"].append({
            "question": _text(e.get("question"), p + ".question", issues),
            "tables": _str_list(e.get("tables"), p + ".tables", issues),
            "note": _text(e.get("note"), p + ".note", issues),
        })

    tables_raw = raw.get("tables")
    if tables_raw is None:
        issues.append(_issue("error", "tables", "the file has no `tables:` section"))
        tables_raw = {}
    if isinstance(tables_raw, list):  # [{name: x, ...}] form
        converted = {}
        for i, t in enumerate(tables_raw):
            if isinstance(t, dict) and t.get("name"):
                converted[str(t["name"])] = {k: v for k, v in t.items() if k != "name"}
            else:
                issues.append(_issue("error", f"tables[{i}]", "a table entry needs a `name`"))
        tables_raw = converted
    if not isinstance(tables_raw, dict):
        issues.append(_issue("error", "tables", "must be a mapping of table name to its description"))
        tables_raw = {}

    table_known = {"description", "aka", "grain", "notes", "ignore", "columns", "relationships"}
    for tname, t in tables_raw.items():
        tp = f"tables.{tname}"
        if t is None:
            t = {}
        if isinstance(t, str):  # shorthand: "tbl_x: what it holds"
            t = {"description": t}
        if not isinstance(t, dict):
            issues.append(_issue("error", tp, "must be a mapping (or a one-line description)"))
            continue
        for k in t:
            if k not in table_known:
                issues.append(_issue("warning", f"{tp}.{k}", "unknown key (ignored)"))
        entry: dict[str, Any] = {
            "description": _text(t.get("description"), tp + ".description", issues),
            "aka": _str_list(t.get("aka"), tp + ".aka", issues),
            "grain": _text(t.get("grain"), tp + ".grain", issues),
            "notes": _text(t.get("notes"), tp + ".notes", issues),
            "ignore": bool(t.get("ignore", False)),
            "relationships": _str_list(t.get("relationships"), tp + ".relationships", issues),
            "columns": {},
        }
        cols = t.get("columns") or {}
        if isinstance(cols, list):
            cols = {str(c["name"]): {k: v for k, v in c.items() if k != "name"}
                    for c in cols if isinstance(c, dict) and c.get("name")}
        if not isinstance(cols, dict):
            issues.append(_issue("error", tp + ".columns", "must be a mapping of column name to its description"))
            cols = {}
        for cname, c in cols.items():
            cp = f"{tp}.columns.{cname}"
            if isinstance(c, str):
                c = {"description": c}
            if c is None:
                c = {}
            if not isinstance(c, dict):
                issues.append(_issue("error", cp, "must be a mapping (or a one-line description)"))
                continue
            for k in c:
                if k not in {"description", "type", "unit", "values", "lookup", "references", "aka"}:
                    issues.append(_issue("warning", f"{cp}.{k}", "unknown key (ignored)"))
            ctype = _text(c.get("type"), cp + ".type", issues).lower()
            if ctype and ctype not in KNOWN_TYPES:
                issues.append(_issue("warning", cp + ".type",
                                     f"unknown type {ctype!r}; known: {', '.join(sorted(KNOWN_TYPES))}"))
            values = c.get("values")
            if values is not None and not isinstance(values, dict):
                issues.append(_issue("error", cp + ".values", "must be a mapping of stored value to meaning"))
                values = None
            lookup = c.get("lookup")
            if lookup is not None:
                if not isinstance(lookup, dict) or not lookup.get("table"):
                    issues.append(_issue("error", cp + ".lookup", "needs at least `table`"))
                    lookup = None
            entry["columns"][str(cname)] = {
                "description": _text(c.get("description"), cp + ".description", issues),
                "type": ctype,
                "unit": _text(c.get("unit"), cp + ".unit", issues),
                "aka": _str_list(c.get("aka"), cp + ".aka", issues),
                "values": {str(k): str(v) for k, v in (values or {}).items()},
                "lookup": ({str(k): v for k, v in lookup.items()} if lookup else None),
                "references": _text(c.get("references"), cp + ".references", issues),
            }
        schema["tables"][str(tname)] = entry
    return schema, issues


# ---------------------------------------------------------------------------
# Matching against the live catalog
# ---------------------------------------------------------------------------

def _table_names(t: dict[str, Any]) -> list[str]:
    key = t.get("table_key") or ""
    name = t.get("name") or ""
    return [x for x in {key, name, key.split(".")[-1], name.split(".")[-1]} if x]


def match_tables(schema: dict[str, Any], catalog: list[dict[str, Any]]) -> tuple[dict[str, dict], list[dict]]:
    """Map each schema table to its catalog entry. Returns ``(matches, issues)``."""
    issues: list[dict[str, str]] = []
    by_exact: dict[str, list[dict]] = {}
    by_lower: dict[str, list[dict]] = {}
    for t in catalog:
        for n in _table_names(t):
            by_exact.setdefault(n, []).append(t)
            by_lower.setdefault(n.lower(), []).append(t)
    all_names = sorted({n for t in catalog for n in _table_names(t)})
    matches: dict[str, dict] = {}
    for sname in schema["tables"]:
        hits = by_exact.get(sname) or by_lower.get(sname.lower()) or []
        uniq = {id(h): h for h in hits}
        if len(uniq) == 1:
            matches[sname] = next(iter(uniq.values()))
        elif len(uniq) > 1:
            keys = ", ".join(sorted(h.get("table_key", "?") for h in uniq.values()))
            issues.append(_issue("error", f"tables.{sname}", f"ambiguous: matches several tables ({keys}); write the full `schema.table` name"))
        else:
            near = difflib.get_close_matches(sname, all_names, n=3, cutoff=0.6)
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            issues.append(_issue("warning", f"tables.{sname}", f"no such table in the connected source (ignored).{hint}"))
    return matches, issues


def check_against_catalog(schema: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Full validation report: file issues are produced elsewhere; this adds catalog checks."""
    matches, issues = match_tables(schema, catalog)
    for sname, t in matches.items():
        known = {c.get("name", "") for c in (t.get("metadata") or {}).get("columns", [])}
        lower = {k.lower(): k for k in known}
        for cname, c in schema["tables"][sname]["columns"].items():
            if cname not in known and cname.lower() not in lower:
                near = difflib.get_close_matches(cname, sorted(known), n=3, cutoff=0.6)
                hint = f" Did you mean: {', '.join(near)}?" if near else ""
                issues.append(_issue("warning", f"tables.{sname}.columns.{cname}", "no such column (ignored)." + hint))
            ref = c.get("references") or ""
            if ref and not _ref_resolves(ref, catalog):
                issues.append(_issue("warning", f"tables.{sname}.columns.{cname}.references",
                                     f"{ref!r} does not match a table.column in the source"))
            lk = c.get("lookup")
            if lk and not _table_exists(str(lk.get("table")), catalog):
                issues.append(_issue("warning", f"tables.{sname}.columns.{cname}.lookup.table",
                                     f"{lk.get('table')!r} is not a table in the source"))
    documented = {id(t) for t in matches.values()}
    undocumented = sorted(
        (t.get("table_key") or t.get("name") or "?") for t in catalog if id(t) not in documented
    )
    return {
        "issues": issues,
        "matched_tables": len(matches),
        "tables_in_file": len(schema["tables"]),
        "tables_in_catalog": len(catalog),
        "undocumented_tables": undocumented,
    }


def _table_exists(name: str, catalog: list[dict]) -> bool:
    return any(name in _table_names(t) or name.lower() in {n.lower() for n in _table_names(t)} for t in catalog)


def _ref_resolves(ref: str, catalog: list[dict]) -> bool:
    if "." not in ref:
        return _table_exists(ref, catalog)
    table, col = ref.rsplit(".", 1)
    for t in catalog:
        if table in _table_names(t) or table.lower() in {n.lower() for n in _table_names(t)}:
            cols = {c.get("name", "").lower() for c in (t.get("metadata") or {}).get("columns", [])}
            return not cols or col.lower() in cols
    return False


# ---------------------------------------------------------------------------
# Applying the schema to catalog entries
# ---------------------------------------------------------------------------

def _values_text(values: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in values.items())


def apply_schema(tables: list[dict[str, Any]], schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Overlay *schema* on catalog *tables* (mutated, returned). User text always wins."""
    if not schema or not schema.get("tables"):
        return tables
    matches, _ = match_tables(schema, tables)
    glossary_keywords: dict[int, list[str]] = {}
    for g in schema.get("glossary", []):
        for target in g.get("maps_to", []):
            hit = [t for t in tables if target in _table_names(t) or target.lower() in {n.lower() for n in _table_names(t)}]
            for t in hit:
                glossary_keywords.setdefault(id(t), []).extend([g["term"], *g.get("aka", [])])

    for sname, t in matches.items():
        st = schema["tables"][sname]
        meta = t.setdefault("metadata", {})
        meta["schema_documented"] = True
        if st["description"]:
            meta["description"] = st["description"]
            meta["description_source"] = "schema"
        kws = list(dict.fromkeys([*st["aka"], *glossary_keywords.get(id(t), []), *meta.get("keywords", [])]))
        if kws:
            meta["keywords"] = kws
        if st["grain"]:
            meta["grain"] = st["grain"]
        if st["notes"]:
            meta["notes"] = st["notes"]
        if st["ignore"]:
            meta["ignored"] = True

        declared: list[str] = []
        by_lower = {c.lower(): c for c in st["columns"]}
        covered: set[str] = set()
        for col in meta.get("columns", []):
            cname = col.get("name", "")
            sc = st["columns"].get(cname) or st["columns"].get(by_lower.get(cname.lower(), ""))
            if not sc:
                continue
            desc = sc["description"]
            if desc:
                if sc["unit"]:
                    desc = f"{desc} ({sc['unit']})"
                col["description"] = desc
                col["description_source"] = "schema"
            elif sc["unit"]:
                col["description"] = f"unit: {sc['unit']}"
            if sc["type"] in _SEMANTIC_TYPES:
                col["semantic_type"] = sc["type"]
            notes: list[str] = []
            if sc["values"]:
                col["value_meanings"] = _values_text(sc["values"])
                notes.append("values: " + col["value_meanings"])
            if sc["lookup"]:
                lk = sc["lookup"]
                where = ", ".join(f"{k}={v}" for k, v in (lk.get("where") or {}).items())
                notes.append(f"decoded by {lk.get('table')}" + (f" ({lk.get('key')}→{lk.get('label')})" if lk.get("key") and lk.get("label") else "")
                             + (f" where {where}" if where else ""))
            if sc["references"]:
                declared.append(f"{cname} references {sc['references']} (declared)")
                covered.add(cname)
            if sc["aka"]:
                meta["keywords"] = list(dict.fromkeys([*meta.get("keywords", []), *sc["aka"]]))
            if notes:
                col["profile"] = "; ".join([n for n in (col.get("profile"), *notes) if n])
        declared.extend(f"{r} (declared)" for r in st["relationships"])
        if declared:
            inferred = [r for r in meta.get("relationships", [])
                        if not any(r.startswith(c + " ") or r.startswith(c + ":") for c in covered)]
            meta["relationships"] = [*declared, *inferred]
    return tables


def visible_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tables the agent should see: those the schema did not mark ``ignore``."""
    return [t for t in tables if not (t.get("metadata") or {}).get("ignored")]


# ---------------------------------------------------------------------------
# Summary the agent reads in its system prompt
# ---------------------------------------------------------------------------

def build_overview(schema: dict[str, Any], tables: list[dict[str, Any]], *, max_chars: int = MAX_OVERVIEW_CHARS) -> str:
    """Compact, authoritative overview of a documented source for the system prompt."""
    lines: list[str] = []
    if schema.get("description"):
        lines.append(f"  About: {schema['description'].strip()[:600]}")
    if schema.get("rules"):
        lines.append("  Rules (written by the data owner; follow them):")
        lines.extend(f"    - {r}" for r in schema["rules"][:12])
    gl = schema.get("glossary", [])
    if gl:
        lines.append("  Glossary:")
        for g in gl[:15]:
            alias = f" / {', '.join(g['aka'][:4])}" if g.get("aka") else ""
            to = f" → {', '.join(g['maps_to'][:3])}" if g.get("maps_to") else ""
            lines.append(f"    - {g['term']}{alias}{to}" + (f": {g['meaning'][:100]}" if g.get("meaning") else ""))
    docs = [t for t in visible_tables(tables) if (t.get("metadata") or {}).get("schema_documented")]
    if docs:
        lines.append("  Documented tables:")
        for t in docs:
            m = t.get("metadata") or {}
            name = (t.get("table_key") or t.get("name") or "?")
            lines.append(f"    - {name}: {(m.get('description') or '').strip()[:110]}"
                         + (f" [{m['grain'][:60]}]" if m.get("grain") else ""))
    ex = schema.get("examples", [])
    if ex:
        lines.append("  Worked examples:")
        for e in ex[:6]:
            lines.append(f"    - “{e['question'][:100]}” → {', '.join(e['tables'][:6])}" + (f" ({e['note'][:100]})" if e.get("note") else ""))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "\n    ... (truncated)"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_path(user_home: Path | str, source_id: str, *, mkdir: bool = False) -> Path:
    from data_formulator.datalake.catalog_cache import _cache_filename
    d = Path(user_home) / "catalog_schema"
    if mkdir:
        d.mkdir(parents=True, exist_ok=True)
    return d / _cache_filename(source_id)


def load_schema(user_home: Path | str, source_id: str) -> dict[str, Any] | None:
    try:
        with open(_store_path(user_home, source_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("schema") if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        logger.debug("Failed to read schema for %s", source_id, exc_info=True)
        return None


def load_schema_record(user_home: Path | str, source_id: str) -> dict[str, Any] | None:
    try:
        with open(_store_path(user_home, source_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("schema") else None
    except Exception:
        return None


def save_schema(user_home: Path | str, source_id: str, schema: dict[str, Any], *, filename: str = "") -> None:
    path = _store_path(user_home, source_id, mkdir=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"source_id": source_id, "filename": filename, "saved_at": int(time.time()), "schema": schema},
                  f, ensure_ascii=False)
    tmp.replace(path)


def delete_schema(user_home: Path | str, source_id: str) -> bool:
    try:
        _store_path(user_home, source_id).unlink()
        return True
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Template: the file to fill in
# ---------------------------------------------------------------------------

def _q(value: Any) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(str(value), ensure_ascii=False)


def _flow(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_template(
    catalog: list[dict[str, Any]],
    *,
    source_label: str = "",
    existing: dict[str, Any] | None = None,
) -> str:
    """YAML text to fill in. Pre-fills what is already known and adds hints as comments.

    Starting from the tables' current metadata (database comments, generated
    descriptions, profile facts, inferred relationships) makes the person's job
    correcting rather than writing from nothing. With *existing*, their saved text is
    kept, so a downloaded template round-trips an uploaded file.
    """
    ex = existing or {"tables": {}, "rules": [], "glossary": [], "examples": []}
    out: list[str] = [
        "# Schema file — tell the agents what only you know about this database.",
        "# Fill in what you can; anything left empty falls back to what the system measured.",
        "# Reference: docs/schema-file.md.   Lines starting with # are hints, not content.",
        "version: 1",
        f"name: {_q(ex.get('name') or source_label or '')}",
        f"language: {_q(ex.get('language') or '')}   # language of your descriptions, e.g. fa, en",
        "description: " + _q(ex.get("description") or "") + "   # what this database is for, in 1-3 sentences",
        "rules:   # conventions every query must respect (units, date formats, status meanings…)",
    ]
    for r in ex.get("rules") or []:
        out.append(f"  - {_q(r)}")
    if not ex.get("rules"):
        out.append('  # - "Amounts are in Rials; divide by 10 for Toman."')
        out.append('  # - "Dates are Jalali strings YYYY/MM/DD; never compare with Gregorian dates."')
    out.append("glossary:   # business words (in any language) and where they live")
    for g in ex.get("glossary") or []:
        out.append(f"  - {{term: {_q(g['term'])}, aka: {_flow(g.get('aka', []))}, maps_to: {_flow(g.get('maps_to', []))}, meaning: {_q(g.get('meaning', ''))}}}")
    if not ex.get("glossary"):
        out.append('  # - {term: "مشتری", aka: ["customer", "خریدار"], maps_to: ["tbl_moshtari"], meaning: "a person or shop that buys"}')
    out.append("examples:   # optional: typical questions and the tables that answer them")
    for e in ex.get("examples") or []:
        out.append(f"  - {{question: {_q(e['question'])}, tables: {_flow(e.get('tables', []))}, note: {_q(e.get('note', ''))}}}")
    if not ex.get("examples"):
        out.append('  # - {question: "monthly sales by city", tables: ["tbl_faktor", "tbl_moshtari", "tbl_shahr"], note: "join on moshtari_id, shahr_id"}')
    out.append("")
    out.append("tables:")
    for t in sorted(catalog, key=lambda x: (x.get("table_key") or x.get("name") or "")):
        meta = t.get("metadata") or {}
        key = t.get("table_key") or t.get("name") or ""
        short = key.split(".")[-1]
        prior = (ex.get("tables") or {}).get(key) or (ex.get("tables") or {}).get(short) or {}
        hint_bits = []
        if meta.get("row_count"):
            hint_bits.append(f"~{int(meta['row_count']):,} rows")
        # Pre-fill only text a person wrote (their saved file, or a comment stored in the
        # database). A model-generated description is offered as a hint instead: filling
        # it in would turn an unchecked guess into "authoritative" on upload.
        generated = meta.get("description") if meta.get("description_source") == "auto" else ""
        shown_desc = prior.get("description") or ("" if generated else (meta.get("description") or ""))
        out.append(f"  {_q(short)}:" + (f"   # {', '.join(hint_bits)}" if hint_bits else ""))
        if generated and not prior.get("description"):
            out.append(f"    # generated suggestion (check before using): {re.sub(chr(10), ' ', generated)[:220]}")
        out.append(f"    description: {_q(shown_desc)}")
        out.append(f"    aka: {_flow(prior.get('aka') or [])}")
        out.append(f"    grain: {_q(prior.get('grain') or '')}   # what one row is, e.g. \"one row per invoice line\"")
        if prior.get("notes"):
            out.append(f"    notes: {_q(prior['notes'])}")
        out.append(f"    ignore: {'true' if prior.get('ignore') else 'false'}   # true hides logs, temp and archive tables from the agents")
        rels = [r for r in (meta.get("relationships") or []) if "(declared)" not in r]
        if rels:
            out.append("    # inferred relationships (confirm with `references:` on the column): " + "; ".join(rels[:6]))
        out.append("    columns:")
        for col in meta.get("columns") or []:
            cname = col.get("name", "")
            pc = (prior.get("columns") or {}).get(cname) or {}
            facts = (col.get("profile") or "").replace("\n", " ")
            facts = re.sub(r"\s+", " ", facts)[:150]
            out.append(f"      {_q(cname)}:" + (f"   # {col.get('type', '')}" + (f" | {facts}" if facts else "")))
            col_generated = col.get("description") if col.get("description_source") == "auto" else ""
            if col_generated and not pc.get("description"):
                out.append(f"        # generated suggestion: {re.sub(chr(10), ' ', col_generated)[:160]}")
            out.append(f"        description: {_q(pc.get('description') or ('' if col_generated else (col.get('description') or '')))}")
            if pc.get("type"):
                out.append(f"        type: {_q(pc['type'])}")
            elif col.get("semantic_type"):
                out.append(f"        type: {_q(col['semantic_type'])}   # detected; delete if wrong")
            if pc.get("unit"):
                out.append(f"        unit: {_q(pc['unit'])}")
            if pc.get("values"):
                out.append(f"        values: {_flow(pc['values'])}")
            if pc.get("lookup"):
                out.append(f"        lookup: {_flow(pc['lookup'])}")
            if pc.get("references"):
                out.append(f"        references: {_q(pc['references'])}")
    return "\n".join(out) + "\n"
