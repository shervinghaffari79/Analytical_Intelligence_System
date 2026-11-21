# Schema files: tell the agents about your database once

When you connect a new database, the agents have to work out what its tables mean. Without help
they explore it: list tables, read columns, sample values, guess. That is slow (minutes, many model
calls) and approximate, and it cannot know what only a person knows — that `vaziat = 3` means
"cancelled", that amounts are in Rials, that `t_prs01` is the sales-visit log, that `tbl_log` is noise.

A **schema file** lets you say it once. The system checks it against the live database, stores it
with the connection, and from then on the agents read your descriptions *instead of* exploring.

## Quick start

1. Connect the database (Data & sources → Connect a new source).
2. On that source click **Schema file → Download template**. You get a YAML file with every table and
   column already listed, with row counts, value ranges and examples as `#` hints, and any comments
   stored in the database pre-filled.
3. Fill in what you know. Leave the rest empty — empty means "fall back to what the system measured".
4. **Upload schema…**. The report shows what matched, and warns about typos with suggestions
   ("`tbl_fakter` — did you mean `tbl_faktor`?"). Errors block applying; warnings do not.
5. **Apply schema.** Ask your question; the agent now starts from your text.

Downloading the template again after uploading gives you your own file back, so you can keep editing it.

No time to write prose? Upload a **CSV data dictionary** instead — one row per column:

```csv
table,column,description,type,unit,values,references,table_description
tbl_faktor,vaziat,وضعیت فاکتور,code,,1=تایید شده; 2=در انتظار پرداخت; 3=لغو شده,,فاکتورهای فروش
tbl_faktor,tarikh,تاریخ فاکتور,jalali_date,,,,
tbl_faktor,jam_kol,مبلغ کل,money,ریال,,,
```

Only `table` and `column` are required. UTF-8 and Windows-1256 (Excel's Persian export) both work.

## The YAML format

```yaml
version: 1
name: Sales ERP                     # optional label
language: fa                        # language of your descriptions
description: >-                     # what the database is for
  Operational database of a food distributor: sales, customers, stock, purchasing.

rules:                              # conventions every query must follow; shown to both agents
  - "Amounts are in Rials; divide by 10 for Toman."
  - "Dates are Jalali strings YYYY/MM/DD; never compare them with Gregorian dates."
  - "Exclude cancelled invoices (vaziat = 3) from sales unless asked."

glossary:                           # business words in any language -> where they live
  - {term: مشتری, aka: [customer, خریدار], maps_to: [tbl_moshtari], meaning: "a shop or person who buys"}

examples:                           # optional: typical questions and the tables that answer them
  - {question: "sales by customer city, 1402", tables: [tbl_faktor, tbl_moshtari, tbl_shahr],
     note: "faktor.moshtari_id -> moshtari.shahr_id -> shahr"}

tables:
  tbl_faktor:
    description: "Sales invoice headers; the lines are in tbl_faktor_riz"
    aka: [invoice, فاکتور]          # extra search words for this table
    grain: "one row per invoice"    # what one row is
    notes: "Use this table, not tbl_faktor_old_1399, from 1401 on."
    ignore: false                   # true hides logs / temp / archive tables from the agents
    columns:
      moshtari_id: {description: "customer", references: tbl_moshtari.moshtari_id}
      tarikh:      {description: "invoice date", type: jalali_date}
      jam_kol:     {description: "invoice total", unit: ریال}
      vaziat:
        description: "invoice status"
        values: {1: تایید شده, 2: در انتظار پرداخت, 3: لغو شده}
        lookup: {table: tbl_code_paye, key: code, label: sharh, where: {noe: vaziat_faktor}}
  tbl_shahr: "Cities"               # one-line shorthand for a table with only a description
  tbl_log: {ignore: true}
```

A complete example for the Persian test database is in
[`tools/persian_testdb/pakhsh_schema.yaml`](../tools/persian_testdb/pakhsh_schema.yaml).

### Fields

| Where | Field | Meaning |
|---|---|---|
| top | `description`, `rules`, `glossary`, `examples` | Shown to the data agent for every question. Keep `rules` short and imperative. |
| table | `description`, `aka`, `grain`, `notes` | Searchable; shown by `describe_data` and to the analyst. |
| table | `ignore` | Hides the table from the agents' search and listings (the UI still shows it). |
| table | `relationships` | Extra relationships as text, e.g. `"a.x -> b.y"`. |
| column | `description`, `unit`, `aka` | `unit` is appended: `مبلغ کل (ریال)`. |
| column | `type` | One of `jalali_date`, `jalali_date_int`, `jalali_year`, `gregorian_date`, `time`, `iran_mobile`, `national_id`, `flag`, `code`, `category` (these change how the analyst queries the column), or `id`, `money`, `quantity`, `percent`, `text`, `number`, `date` (informational). |
| column | `values` | What stored codes mean. The analyst sees `1=تایید شده; 2=…` instead of bare numbers, and a search for "لغو شده" finds the table. |
| column | `lookup` | Where the meanings live when they are in another table. |
| column | `references` | A declared foreign key (`table.column`). Replaces the inferred guess for that column. |

Tables may also be written as a list (`- name: tbl_x`), and columns as a list (`- name: c`).

## How it is used

- **Precedence:** your text beats comments stored in the database, which beat model-generated
  descriptions. Anything you leave empty falls back to those. Your file survives a catalog re-sync.
- **Data agent:** the connected-sources summary in its prompt is marked *user-provided schema —
  authoritative* and lists your About text, rules, glossary, documented tables and examples. It is told
  to pick tables from that list, follow the rules, and skip crawling.
- **Search:** `find_data` matches `aka`, glossary terms, value meanings, grain and notes, in any
  language, and Arabic/Persian spelling variants (ي/ی, ك/ک, digits, ZWNJ).
- **Analyst:** after tables are loaded it sees column descriptions, value meanings, units, row grain,
  relationships and your rules next to the data.
- **Generated descriptions** are skipped for tables you documented (the model is not asked to guess
  what you already told it).

## Validation

Errors (the file is not applied): unreadable YAML/JSON, no `tables:`, wrong shapes (`values` must be a
mapping), an ambiguous table name (write `schema.table`), or no table matching the source at all.
Warnings (applied anyway): tables or columns that do not exist (with suggestions), unknown keys or
types, `references` / `lookup` targets that are not in the source.

## API

`POST /api/connectors/schema/{template,validate,save,get,delete}` with `{"connector_id": …}`;
`validate` and `save` take either a multipart `file` or `{"content": …, "filename": …}`; files up to 2 MB.

## Privacy

The contents are sent to the language model as part of its prompts. Do not put passwords or personal
data in descriptions or examples.
