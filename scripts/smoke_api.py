"""接口冒烟测试：不依赖前端，直接打 FastAPI。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import main


def show(label, resp, limit=600):
    text = resp.text if isinstance(resp.text, str) else str(resp)
    print(f"--- {label} [{resp.status_code}] {text[:limit]}")


with TestClient(main.app) as client:
    show("GET /api/health", client.get("/api/health"))
    show("GET /api/meta", client.get("/api/meta"), 900)
    show("GET /api/settings", client.get("/api/settings"), 700)
    show("GET /api/service/status", client.get("/api/service/status"), 700)
    show("GET /api/install/state", client.get("/api/install/state"), 700)
    show("GET /api/bridge/status", client.get("/api/bridge/status"), 700)
    show("GET /api/config/snowluma", client.get("/api/config/snowluma"), 500)
    show("GET /api/credential/snowluma", client.get("/api/credential/snowluma"), 400)
    show("POST /api/credential/snowluma/reset", client.post("/api/credential/snowluma/reset"), 400)
    show("PUT /api/settings deploy_mode", client.put("/api/settings", json={"astrbot": {"deploy_mode": "uv"}}), 400)
    show("PUT /api/settings bad host", client.put("/api/settings", json={"console": {"host": "0.0.0.0"}}), 400)
    show("POST /api/env/install none", client.post("/api/env/install", json={}), 300)
    show("GET /api/service/astrbot/log/recent", client.get("/api/service/astrbot/log/recent?tail=5"), 300)
    show("GET /api/service/nope/start", client.post("/api/service/nope/start"), 300)
