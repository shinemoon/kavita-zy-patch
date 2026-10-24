import json, sys, urllib.request, urllib.error, urllib.parse

BASE = "http://127.0.0.1:5000"
ENV = dict(l.split('=', 1) for l in open('/home/youruser/.picoclaw/workspace/secrets/kavita/.env').read().splitlines() if '=' in l)
KEY = ENV['KAVITA_API_KEY'].strip()


def call(path, method="GET", body=None, headers=None, raw=False):
    h = {"x-api-key": KEY, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return r.status, (b if raw else b[:3000].decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500].decode('utf-8', 'replace')


if __name__ == "__main__":
    path = sys.argv[1]
    method = sys.argv[2] if len(sys.argv) > 2 else "GET"
    print(call(path, method))
