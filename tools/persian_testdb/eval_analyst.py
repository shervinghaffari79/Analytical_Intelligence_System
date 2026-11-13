"""End-to-end check of the analysis stage on the Persian test database.

Imports the tables a question needs through the connector, asks the analyst
agent the question, and compares the chart's data with ground truth computed
directly in SQL.

    python tools/persian_testdb/eval_analyst.py [--mode standard|mini]
"""

import argparse
import json
import time
import urllib.error
import urllib.request

import pymysql

CASES = [
    {
        "id": "sales_by_city_1402",
        "question": "مجموع مبلغ فروش (jam_kol) سال ۱۴۰۲ را به تفکیک شهر مشتری در یک نمودار میله‌ای نشان بده.",
        "tables": ["tbl_faktor", "tbl_moshtari", "tbl_shahr"],
        "truth_sql": """SELECT s.shahr_name k, SUM(f.jam_kol) v FROM tbl_faktor f
                        JOIN tbl_moshtari m ON m.moshtari_id=f.moshtari_id
                        JOIN tbl_shahr s ON s.shahr_id=m.shahr_id
                        WHERE f.tarikh LIKE '1402/%%' GROUP BY s.shahr_name""",
    },
    {
        "id": "qty_by_category",
        "question": "Show the total quantity sold (tedad) for each product category name as a bar chart.",
        "tables": ["tbl_faktor_riz", "tbl_kala", "tbl_goroh_kala"],
        "truth_sql": """SELECT g.goroh_name k, SUM(r.tedad) v FROM tbl_faktor_riz r
                        JOIN tbl_kala k ON k.kala_id=r.kala_id
                        JOIN tbl_goroh_kala g ON g.goroh_id=k.goroh_id GROUP BY g.goroh_name""",
    },
    {
        "id": "invoices_by_status",
        "question": "تعداد فاکتورها به تفکیک وضعیت فاکتور (با عنوان فارسی وضعیت، نه کد) را نشان بده.",
        "tables": ["tbl_faktor", "tbl_code_paye"],
        "truth_sql": """SELECT c.sharh k, COUNT(*) v FROM tbl_faktor f
                        JOIN tbl_code_paye c ON c.noe='vaziat_faktor' AND c.code=f.vaziat GROUP BY c.sharh""",
    },
]


def compare(rows, truth):
    """Match chart rows against {key: value}; keys and values may sit in any column."""
    if not rows:
        return False, "no rows"
    want = {str(k): float(v) for k, v in truth.items()}
    got = {}
    for r in rows:
        keys = [str(v) for v in r.values() if isinstance(v, str)]
        nums = [float(v) for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        for k in keys:
            if k in want:
                got[k] = nums
    hit = sum(1 for k, v in want.items() if any(abs(n - v) <= 1e-6 * max(1.0, abs(v)) for n in got.get(k, [])))
    return hit == len(want), f"{hit}/{len(want)} groups match ground truth ({len(rows)} rows in chart)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5567")
    ap.add_argument("--workspace", default="persian_analyst")
    ap.add_argument("--mode", default="standard")
    ap.add_argument("--database", default="pakhsh_db")
    a = ap.parse_args()
    H = {"X-Workspace-Id": a.workspace, "Content-Type": "application/json"}

    def call(path, payload=None, timeout=900):
        req = urllib.request.Request(a.base + path, headers=H,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode()[:300]}") from None

    model = json.load(call("/api/agent/list-global-models"))["data"][0]
    params = {"host": "127.0.0.1", "port": 3306, "user": "df", "password": "dfpass", "database": a.database}
    cid = "mysql:pakhsh-eval"
    try:
        call("/api/connectors", {"loader_type": "mysql", "display_name": "pakhsh eval", "params": params, "persist": True})
    except RuntimeError:
        pass
    call("/api/connectors/connect", {"connector_id": cid, "params": params})
    call("/api/connectors/sync-catalog-metadata", {"connector_id": cid})
    json.load(call("/api/connectors/profile-catalog", {"connector_id": cid}))

    db = pymysql.connect(host="127.0.0.1", user="df", password="dfpass", database=a.database, charset="utf8mb4")
    passed = 0
    for case in CASES:
        names = []
        for t in case["tables"]:
            r = json.load(call("/api/connectors/import-data",
                               {"connector_id": cid, "source_table": f"{a.database}.{t}"}))
            names.append(r["data"]["table_name"])
        cur = db.cursor()
        cur.execute(case["truth_sql"])
        truth = {k: v for k, v in cur.fetchall()}

        t0 = time.time()
        resp = call("/api/agent/analyst-streaming", {
            "model": model, "agent_mode": a.mode, "max_iterations": 5,
            "input_tables": [{"name": n} for n in names], "user_question": case["question"]})
        rows, code, status, kinds = None, "", "", []
        for line in resp:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            kinds.append(ev.get("type"))
            if ev.get("type") == "result":
                res = (ev.get("content") or {}).get("result") or {}
                rows = (res.get("content") or {}).get("rows")
                code = res.get("code", "")
                status = res.get("status", "")
            elif ev.get("type") == "completion":
                status = status or ev.get("status", "")
        ok, note = compare(rows, truth)
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']:22s} {time.time() - t0:6.1f}s status={status} {note}")
        if not ok:
            print("   events:", [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k])
            print("   chart rows:", json.dumps((rows or [])[:4], ensure_ascii=False)[:400])
            print("   truth     :", json.dumps(dict(list(truth.items())[:4]), ensure_ascii=False, default=float)[:300])
            print("   code      :", code.replace("\n", " | ")[:500])
    print(f"\n== analyst[{a.mode}]: {passed}/{len(CASES)} passed ==")


if __name__ == "__main__":
    main()
