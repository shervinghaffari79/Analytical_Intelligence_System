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


def _response_schema(column_names: list[str]) -> dict[str, Any]:
    """JSON schema for the reply; column keys are pinned to the real columns."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "table_description",
            "schema": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "table_local": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "columns": {
                        "type": "object",
                        "properties": {n: {"type": "string"} for n in column_names[:60]},
                    },
                },
                "required": ["table", "table_local", "keywords", "columns"],
            },
        },
    }


def _validate(parsed: Any) -> str:
    """Return an error message for the model, or '' when the reply is usable."""
    if not isinstance(parsed, dict):
        return "the reply did not contain a JSON object"
    if not _clean(parsed.get("table"), 400) and not _clean(parsed.get("table_local"), 400):
        return 'the "table" description is missing or empty'
    if not isinstance(parsed.get("columns"), dict):
        return '"columns" must be an object mapping column names to descriptions'
    return ""


class CatalogDescribeAgent:
    """Turn a table profile into a description via one LLM call."""

    def __init__(self, client: Any):
        self.client = client
        self._schema_supported = True

    def _complete(self, messages: list[dict[str, Any]], response_format: dict[str, Any]) -> str | None:
        """One completion; schema-constrained when the provider supports it."""
        if self._schema_supported:
            try:
                response = self.client.get_completion(messages=messages, response_format=response_format)
                return response.choices[0].message.content or ""
            except Exception as exc:
                # Provider rejected the schema (or the call failed): remember
                # and retry unconstrained rather than lose the table.
                logger.info("Schema-constrained description failed (%s); using plain prompting", type(exc).__name__)
                self._schema_supported = False
        try:
            response = self.client.get_completion(messages=messages)
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("Catalog description call failed: %s", type(exc).__name__)
            logger.debug("Catalog description failure detail", exc_info=True)
            return None

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
        column_names = [c.get("name") for c in (table.get("metadata") or {}).get("columns", []) if c.get("name")]

        # Structured output, the way LangChain's ProviderStrategy/ToolStrategy
        # split does it: ask the provider to constrain the reply to a JSON
        # schema when it can (Ollama, OpenAI, ...), fall back to plain prompting
        # when it cannot, and on an invalid reply feed the error back once.
        parsed: dict[str, Any] | None = None
        error = ""
        for attempt in range(2):
            content = self._complete(messages, _response_schema(column_names))
            if content is None:
                return None
            parsed = next((o for o in extract_json_objects(content) if isinstance(o, dict)), None)
            error = _validate(parsed)
            if not error:
                break
            logger.info("Catalog description attempt %d invalid: %s", attempt + 1, error)
            messages = messages + [
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": f"Error: {error}. Reply again with one valid JSON object in the required shape."},
            ]
        if error or not parsed:
            return None

        en = _clean(parsed.get("table"), 400)
        local = _clean(parsed.get("table_local"), 400)
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
