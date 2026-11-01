import json
import urllib.request
from pathlib import Path

TOK = Path("/vol2/1001/kavita-zy-patch/state/token").read_text().strip()


def call(path, body):
    req = urllib.request.Request(
        "http://127.0.0.1:5111" + path,
        data=json.dumps(body).encode(),
        headers={"X-ZY-Token": TOK, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


for p in ("/zy/api/auto/pull", "/zy/api/auto/push"):
    d = call(p, {"series_id": 27})
    keep = {k: d.get(k) for k in ("ok", "pull", "push", "notes", "time")}
    print(p, json.dumps(keep, ensure_ascii=False)[:700])
