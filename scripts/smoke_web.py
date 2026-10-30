import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)
out = {}

r = client.get("/")
out["root"] = [r.status_code, len(r.text)]
m = re.search(r'src="(/assets/[^"]+)"', r.text)
if m:
    a = client.get(m.group(1))
    out["asset"] = [m.group(1), a.status_code, len(a.content)]
m2 = re.search(r'href="(/assets/[^"]+)"', r.text)
if m2:
    c = client.get(m2.group(1))
    out["css"] = [m2.group(1), c.status_code, len(c.content)]
out["spa_fallback"] = client.get("/some/deep/route").status_code
out["api_meta"] = client.get("/api/meta").status_code
out["health"] = client.get("/api/health").status_code
out["has_app_div"] = '<div id="app">' in r.text
print(json.dumps(out, ensure_ascii=False, indent=2))