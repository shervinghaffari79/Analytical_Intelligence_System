"""Measure how well the data-loading agent finds the right tables in the
Persian test database (see ``make_db.py``).

For every question the agent runs one turn through the real HTTP endpoint and
must end with a load plan. A question passes when every required table is among
the selected candidates. Run it before and after ``profile-catalog`` to see what
the derived metadata buys.

    python tools/persian_testdb/eval_agent.py --label baseline
    python tools/persian_testdb/eval_agent.py --label profiled --profile
    python tools/persian_testdb/eval_agent.py --label described --profile --describe
"""

import argparse
import json
import time
import urllib.error
import urllib.request

# (id, question, required tables, check)  — check: optional predicate on the plan
QUESTIONS = [
    ("sales_by_city_fa", "می‌خواهم فروش سال ۱۴۰۲ را به تفکیک شهر مشتری تحلیل کنم.",
     {"tbl_faktor", "tbl_moshtari", "tbl_shahr"}, "jalali_1402"),
    ("category_sales_en", "Which product categories sell the most? Load what I need to analyze it.",
     {"tbl_faktor_riz", "tbl_kala", "tbl_goroh_kala"}, None),
    ("stock_fa", "موجودی انبارها به تفکیک کالا را بارگذاری کن.",
     {"tbl_mojodi", "tbl_kala"}, None),
    ("salary_fa", "حقوق پرداختی به کارمندان هر شعبه را می‌خواهم بررسی کنم.",
     {"tbl_hoghogh", "tbl_karmand"}, None),
    ("returns_en", "I want to look at returned goods and the reasons for the returns.",
     {"tbl_bargasht"}, None),
    ("expenses_fa", "هزینه‌های شعبه‌ها به تفکیک نوع هزینه.",
     {"tbl_hazine", "tbl_noe_hazine"}, None),
    ("visits_en", "Load the data on sales reps' visits to customers and how the visits turned out.",
     {"t_prs01"}, None),
    ("purchases_fa", "خریدهای انجام شده از تامین‌کنندگان را بارگذاری کن.",
     {"tbl_kharid", "tbl_tamin_konande"}, None),
    ("top_customers_en", "Who are our top customers by total invoice amount?",
     {"tbl_faktor", "tbl_moshtari"}, None),
    ("stock_moves_fa", "گردش انبار یعنی ورود و خروج کالا را می‌خواهم.",
     {"tbl_gardesh_anbar"}, None),
    ("cheque_en", "Load only the invoices that were paid by cheque.",
     {"tbl_faktor"}, "cheque"),
    ("karaj_fa", "مشتریان شهر کرج را بارگذاری کن.",
     {"tbl_moshtari"}, None),
]


def short(key: str) -> str:
    return str(key).split(".")[-1]


def check_plan(kind, candidates):
    """Extra correctness checks on filters. Returns (ok, note)."""
    if kind == "jalali_1402":
        f = [x for c in candidates if short(c.get("table_key")) == "tbl_faktor" for x in c.get("filters", [])]
        vals = json.dumps([x.get("value") for x in f], ensure_ascii=False)
        if not f:
            return True, "no date filter (loads all years)"
        if "1402" in vals or "۱۴۰۲" in vals:
            return True, f"jalali filter {vals}"
        return False, f"non-Jalali date filter {vals}"
    if kind == "cheque":
        f = [x for c in candidates if short(c.get("table_key")) == "tbl_faktor" for x in c.get("filters", [])]
        ok = any(x.get("column") == "noe_pardakht" and str(x.get("value")) in ("3", "[3]") for x in f)
        return ok, "filters " + json.dumps(f, ensure_ascii=False)
    return True, ""


class Api:
    def __init__(self, base, workspace):
        self.base, self.h = base, {"X-Workspace-Id": workspace, "Content-Type": "application/json"}

    def call(self, path, payload=None, timeout=900):
        req = urllib.request.Request(self.base + path, headers=self.h,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode()[:400]}") from None

    def json(self, path, payload=None, timeout=900):
        return json.load(self.call(path, payload, timeout))


def run_question(api, model, question):
    t0 = time.time()
    tools, plan, text = [], None, ""
    resp = api.call("/api/agent/data-loading-chat",
                    {"model": model, "messages": [{"role": "user", "content": question}]})
    for line in resp:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        kind = ev.get("type")
        if kind == "tool_start" or (kind == "tool_result" and not tools):
            pass
        if kind == "tool_result":
            tools.append(ev.get("tool"))
        elif kind == "actions":
            for a in ev.get("actions", []):
                if a.get("type") == "load_plan":
                    plan = a
        elif kind == "done":
            text = ev.get("full_text", "")
        elif kind == "error":
            text = "ERROR " + json.dumps(ev, ensure_ascii=False)[:300]
    return {"tools": tools, "plan": plan, "text": text, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5567")
    ap.add_argument("--workspace", default="persian_eval")
    ap.add_argument("--label", default="run")
    ap.add_argument("--db-host", default="127.0.0.1")
    ap.add_argument("--db-user", default="df")
    ap.add_argument("--db-password", default="dfpass")
    ap.add_argument("--database", default="pakhsh_db")
    ap.add_argument("--profile", action="store_true", help="run profile-catalog first")
    ap.add_argument("--describe", action="store_true", help="also generate LLM descriptions")
    ap.add_argument("--reset", action="store_true", help="drop stored profiles first (re-sync only)")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    api = Api(a.base, a.workspace)
    model = api.json("/api/agent/list-global-models")["data"][0]
    params = {"host": a.db_host, "port": 3306, "user": a.db_user, "password": a.db_password, "database": a.database}
    cid = "mysql:pakhsh-eval"
    try:
        api.json("/api/connectors", {"loader_type": "mysql", "display_name": "pakhsh eval", "params": params, "persist": True})
    except RuntimeError:
        pass
    api.json("/api/connectors/connect", {"connector_id": cid, "params": params})
    api.json("/api/connectors/sync-catalog-metadata", {"connector_id": cid})

    if a.profile:
        t0 = time.time()
        body = {"connector_id": cid, "refresh": a.reset}
        if a.describe:
            body["model"] = model
        while True:
            r = api.json("/api/connectors/profile-catalog", body, timeout=1800)["data"]
            print(f"profile-catalog: {json.dumps(r)}  ({time.time() - t0:.0f}s)", flush=True)
            body["refresh"] = False
            if not r.get("remaining"):
                break

    only = {x for x in a.only.split(",") if x}
    results, passed = [], 0
    for qid, q, required, kind in QUESTIONS:
        if only and qid not in only:
            continue
        try:
            r = run_question(api, model, q)
        except Exception as exc:  # keep going; one failure must not hide the rest
            r = {"tools": [], "plan": None, "text": f"EXC {exc}", "seconds": 0}
        cands = (r["plan"] or {}).get("candidates", [])
        selected = {short(c.get("table_key")) for c in cands if c.get("selected") and not c.get("resolution_error")}
        missing = sorted(required - selected)
        extra = sorted(selected - required)
        ok_filter, note = check_plan(kind, cands) if r["plan"] else (False, "")
        ok = bool(r["plan"]) and not missing and ok_filter
        passed += ok
        results.append({"id": qid, "ok": ok, "missing": missing, "extra": extra, "selected": sorted(selected),
                        "note": note, "tools": r["tools"], "seconds": r["seconds"], "text": r["text"][:300]})
        print(f"[{'PASS' if ok else 'FAIL'}] {qid:18s} {r['seconds']:6.1f}s tools={len(r['tools']):2d} "
              f"selected={sorted(selected)} missing={missing} {note}", flush=True)
        if not r["plan"]:
            print("        no load plan; reply:", r["text"][:200].replace("\n", " "), flush=True)
    total = len(results)
    print(f"\n== {a.label}: {passed}/{total} passed ==")
    if a.out:
        json.dump({"label": a.label, "passed": passed, "total": total, "results": results},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
