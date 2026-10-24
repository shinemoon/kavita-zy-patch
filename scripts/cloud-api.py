import gzip
import json
import sys
import urllib.parse
import urllib.request

A = json.load(open("/vol2/1001/zhangyue-bridge/.auth.json"))
BASE = "https://icloud.zhangyue.com"


def call(path, extra=None, method="GET", body=None):
    qs = {
        "usr": A["usr"], "zysid": A["zysid"],
        "p29": A["p29"], "p2": A["p2"], "p3": A["p3"], "p4": A["p4"],
        "p33": A["p33"],
    }
    if extra:
        qs.update(extra)
    url = BASE + path + "?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": "iReader/8.0", "Accept-Encoding": "gzip"})
    if body is not None:
        req.data = body if isinstance(body, bytes) else urllib.parse.urlencode(body).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace")
    except Exception as e:
        return None, f"ERR {e}"


if __name__ == "__main__":
    path = sys.argv[1]
    extra = dict(a.split("=", 1) for a in sys.argv[2:]) if len(sys.argv) > 2 else None
    st, txt = call(path, extra)
    print("STATUS", st)
    print(txt[:4000])
