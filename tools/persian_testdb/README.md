# Persian test database

A curated MySQL/MariaDB database for testing the agents against the kind of
source that is common in practice and hard for a model: no table or column
comments, no declared foreign keys, transliterated ("Finglish") or opaque
identifiers, Persian values with mixed Arabic/Persian letter forms and digits,
Jalali dates stored as text and integers, and status codes whose meaning lives
in a generic lookup table. 26 tables, about 80,000 rows, deterministic.

| Script | What it does |
|---|---|
| `make_db.py` | Creates and fills the database (`pakhsh_db`). |
| `eval_agent.py` | Asks the data-loading agent 12 questions (Persian and English) and checks that the load plan contains the right tables and filters. `--profile` runs catalog profiling first; `--describe` also generates descriptions with the configured model. |
| `eval_analyst.py` | Imports the tables for 3 questions, runs the analyst agent, and compares the chart data with ground truth computed in SQL. |

```bash
python tools/persian_testdb/make_db.py --user df --password dfpass
python tools/persian_testdb/eval_agent.py --label described --profile --describe
python tools/persian_testdb/eval_analyst.py --mode standard
```

Both evaluation scripts need a running Data Formulator server with a model
configured, and talk to it over HTTP.

## Reference results

`qwen3.5:9b` on Ollama, Tesla T4, one run per condition (expect a question or
two of run-to-run variation):

| Condition | Load plans correct |
|---|---|
| Before these changes | 0 / 12 |
| Fixes + profiling only (no generated descriptions) | 4 / 12 |
| Fixes + profiling + generated descriptions | 9 / 12 (two separate runs) |

Analyst stage with the right tables loaded: 3 / 3 match SQL ground truth, in
both `standard` and `mini` modes.
