# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate table and column descriptions for undocumented catalog tables.

Input is the data profile built by ``datalake/catalog_profile.py`` (column
types, value ranges, frequent values, inferred relationships), rendered in an
M-Schema-like layout. Output is a short bilingual description plus search
keywords, so that ``find_data`` can match a request such as "sales by city" or
«فروش به تفکیک شهر» against a table called ``tbl_faktor``.

The model is asked for one table at a time: small local models stay reliable on
a short, self-contained prompt, and a failure costs one table, not the catalog.
"""

from __future__ import annotations

import logging
from typing import Any

from data_formulator.agents.agent_utils import extract_json_objects
from data_formulator.datalake.catalog_profile import column_facts, relationship_lines

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You document database tables that have no documentation.

You are given one table: its name, its columns with data type and facts measured
from the data (value range, frequent or example values, inferred keys), and the
names of the other tables in the same database.

Identifiers are often abbreviations or transliterations of another language
written in Latin letters (for example Persian "Finglish": moshtari = customer,
faktor = invoice, kala = goods/product, anbar = warehouse, tarikh = date,
mablagh = amount, tedad = quantity). Use the example VALUES as the main
evidence for what a column holds; use names as a secondary hint. If a column's
meaning cannot be determined, say what is observable instead of guessing.

Reply with ONE JSON object and nothing else:
{
  "table": "<one or two sentences in English: what one row represents and what the table is used for>",
  "table_local": "<the same, in the language of the data values (e.g. Persian); empty string if the data is English>",
  "keywords": ["<5-12 search terms a user might type for this table, in English AND in the data's language>"],
  "columns": {"<column name>": "<short English meaning; add the local-language term in parentheses when helpful>"}
}
Describe every column. Keep each column description under 15 words. Do not
repeat the measured facts (ranges, examples) — they are shown separately.
"""


def render_table_prompt(
    table: dict[str, Any],
    entry: dict[str, Any],
    other_tables: list[str],
) -> str:
    """Render one table in an M-Schema-like text block."""
    meta = table.get("metadata") or {}
    prof = entry.get("profile") or {}
    col_prof = prof.get("columns") or {}
    name = (table.get("table_key") or table.get("name", "")).split(".")[-1]

    lines = [f"# Table: {name}"]
    if prof.get("row_count") is not None:
        lines.append(f"rows: {prof['row_count']}")
    if meta.get("description") and meta.get("description_source") != "auto":
        lines.append(f"existing description: {meta['description']}")
    lines.append("[")
    for col in meta.get("columns", [])[:60]:
        cname = col.get("name", "")
        bits = [f"{cname}: {col.get('type', '')}"]
        facts = column_facts(col_prof.get(cname, {}))
        if facts:
            bits.append(facts)
        if col.get("description") and col.get("description_source") != "auto":
            bits.append(f"existing description: {col['description']}")
        lines.append("  (" + ", ".join(bits) + ")")
    lines.append("]")
    rel = relationship_lines(entry)
    if rel:
        lines.append("Inferred relationships:")
        lines.extend(f"  {r}" for r in rel)
    if other_tables:
        lines.append("Other tables in this database: " + ", ".join(other_tables[:80]))
    return "\n".join(lines)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


class CatalogDescribeAgent:
    """Turn a table profile into a description via one LLM call."""

    def __init__(self, client: Any):
        self.client = client

    def describe(
        self,
        table: dict[str, Any],
        entry: dict[str, Any],
        other_tables: list[str],
    ) -> dict[str, Any] | None:
        """Return ``{"table", "keywords", "columns"}`` or ``None`` on failure."""
        prompt = render_table_prompt(table, entry, other_tables)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            response = self.client.get_completion(messages=messages)
            content = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("Catalog description call failed: %s", type(exc).__name__)
            logger.debug("Catalog description failure detail", exc_info=True)
            return None

        parsed = next((o for o in extract_json_objects(content) if isinstance(o, dict)), None)
        if not parsed:
            logger.info("Catalog description: no JSON object in model reply")
            return None

        en = _clean(parsed.get("table"), 400)
        local = _clean(parsed.get("table_local"), 400)
        if not en and not local:
            return None
        known = {c.get("name") for c in (table.get("metadata") or {}).get("columns", [])}
        raw_cols = parsed.get("columns") if isinstance(parsed.get("columns"), dict) else {}
        columns = {
            str(k): _clean(v, 160)
            for k, v in raw_cols.items()
            if str(k) in known and _clean(v, 160)
        }
        raw_kw = parsed.get("keywords") if isinstance(parsed.get("keywords"), list) else []
        keywords = list(dict.fromkeys(_clean(k, 40) for k in raw_kw if _clean(k, 40)))[:16]
        return {
            "table": f"{en} — {local}" if en and local else (en or local),
            "keywords": keywords,
            "columns": columns,
        }
