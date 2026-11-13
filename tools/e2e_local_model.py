"""End-to-end check through a running Data Formulator server using its first configured model.

Usage: python tools/e2e_local_model.py [mini] [standard]   (server on http://127.0.0.1:5567)
"""
import io, json, sys, time, urllib.request, uuid

BASE = "http://127.0.0.1:5567"

def call(path, payload=None, headers=None, raw=None, timeout=600):
    h = {"Content-Type": "application/json"} if raw is None else {}
    h["X-Workspace-Id"] = "gpu_e2e_test"
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)

model = json.load(call("/api/agent/list-global-models"))["data"][0]
print("model:", model["id"], model["endpoint"])

t = time.time()
r = json.load(call("/api/agent/test-model", {"model": model}))
print(f"test-model: {r.get('status')} ({time.time()-t:.1f}s)")

t = time.time()
r = json.load(call("/api/agent/sort-data", {"model": model, "field": "month",
     "items": ["Mar", "Jan", "Dec", "Feb", "Jul"]}))
print(f"sort-data: {json.dumps(r)[:300]} ({time.time()-t:.1f}s)")

csv = "product,region,revenue,units\n" + "\n".join(
    f"{p},{rg},{(i*37+j*91)%500+100},{(i*7+j*3)%20+1}"
    for i, p in enumerate(["Laptop", "Phone", "Tablet", "Monitor", "Keyboard"])
    for j, rg in enumerate(["North", "South", "East", "West"]))
b = uuid.uuid4().hex
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"table_name\"\r\n\r\nsales\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sales.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n{csv}\r\n--{b}--\r\n").encode()
r = json.load(call("/api/tables/create-table", raw=body,
                   headers={"Content-Type": f"multipart/form-data; boundary={b}"}))
print("create-table:", json.dumps(r)[:200])
tname = (r.get("data") or r).get("table_name", "sales")

for mode in sys.argv[1:] or ["mini"]:
    t = time.time()
    resp = call("/api/agent/analyst-streaming", {
        "model": model, "agent_mode": mode, "max_iterations": 5,
        "input_tables": [{"name": tname}],
        "user_question": "Show total revenue by product as a bar chart."})
    kinds, last = [], None
    for line in resp:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        k = ev.get("type") or ev.get("event") or "?"
        if not kinds or kinds[-1] != k:
            kinds.append(k)
        if k != "text_delta":
            last = ev
            print(f"  [{mode}] {k}: {json.dumps(ev)[:700]}")
    print(f"analyst-streaming[{mode}] events: {kinds} ({time.time()-t:.1f}s)")
