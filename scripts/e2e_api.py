"""端到端联调：真实起服务，走 HTTP 覆盖验收要点的核心流程。"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8899")
results = []


def rec(name, ok, detail=""):
    results.append({"case": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(detail)[:160] if detail else ""))


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=90.0) as c:
        r = c.get("/")
        rec("GET / 返回前端", r.status_code == 200 and "<div id=\"app\">" in r.text, r.status_code)

        r = c.get("/api/health")
        rec("GET /api/health", r.status_code == 200 and r.json().get("ok"), r.json().get("version"))

        r = c.get("/api/meta")
        meta = r.json()
        rec("GET /api/meta", r.status_code == 200 and meta.get("version") == "0.1.1", meta.get("root"))

        # ---- 环境检测（真实检测，按路线动态显示）
        r = c.get("/api/env/detect", params={"force": "true"})
        det = r.json()
        ids = [x["id"] for x in det.get("checks", [])]
        rec("GET /api/env/detect", r.status_code == 200 and len(ids) > 0, f"{len(ids)} 项 / route={det.get('route')}")
        docker_only = [x for x in det.get("checks", []) if x.get("scope") == "docker"]
        rec("未选路线时 Docker 专属项为待选择", det.get("route") is None and all(x["status"] == "pending" for x in docker_only),
            [x["id"] for x in docker_only])

        # ---- 选择路线（不真正安装，只验证单一口径）
        r = c.post("/api/install/astrbot/switch-mode", json={"mode": "uv"})
        rec("切换路线到 uv", r.status_code == 200 and r.json().get("mode") == "uv", r.json().get("changed"))
        r = c.get("/api/settings")
        rec("settings 读到同一份 deploy_mode", r.json()["settings"]["astrbot"]["deploy_mode"] == "uv")
        r = c.put("/api/settings", json={"astrbot": {"deploy_mode": "docker"}})
        rec("settings 禁止直接改 deploy_mode", r.status_code == 400, r.json().get("code"))

        # ---- 配置读写 + 三次备份 + 还原
        base_cfg = {"dashboard": {"enable": True, "port": 6185}, "platform": []}
        # 先建立初始文件（安装流程会写它），再连续改 3 次，验证每次都留备份。
        c.put("/api/config/astrbot/cmd_config", json={"data": base_cfg, "reason": "init", "backup": False})
        backups = []
        for i in range(3):
            payload = json.loads(json.dumps(base_cfg))
            payload["dashboard"]["port"] = 6185 + i
            r = c.put("/api/config/astrbot/cmd_config", json={"data": payload, "reason": f"e2e-{i}", "backup": True})
            ok = r.status_code == 200
            backups.append((r.json() or {}).get("backup"))
            rec(f"第 {i+1} 次写配置", ok and bool(backups[-1]), backups[-1])
        r = c.get("/api/config/astrbot/backups/list")
        lst = r.json().get("backups", [])
        rec("三次写入生成 ≥3 个备份", len(lst) >= 3, f"{len(lst)} 个")
        r = c.get("/api/config/astrbot/cmd_config")
        rec("配置读回的值正确", r.json()["data"]["dashboard"]["port"] == 6187, r.json()["data"]["dashboard"]["port"])
        rec("配置读取带 deploy_mode", r.json().get("deploy_mode") == "uv", r.json().get("deploy_mode"))
        if lst:
            r = c.post("/api/config/backups/restore", json={"name": lst[0]["name"]})
            rec("从备份还原", r.status_code == 200, lst[0]["name"])

        # ---- 凭据
        r = c.put("/api/credential/snowluma", json={"values": {"http_token": "http-abc", "ws_token": "ws-xyz"}})
        rec("写入 SnowLuma 双 accessToken", r.status_code == 200, r.json().get("updated"))
        r = c.get("/api/credential/snowluma")
        v = r.json().get("values", {})
        rec("读回 SnowLuma 凭据", v.get("http_token") == "http-abc" and v.get("ws_token") == "ws-xyz")
        r = c.post("/api/credential/snowluma/reset", params={"confirm": "true"})
        rec("SnowLuma 不提供控制台重置", r.status_code == 409, r.json().get("code"))
        r = c.get("/api/credential/astrbot")
        rec("AstrBot 凭据可读", r.status_code == 200, r.json().get("fields"))

        # ---- 桥接
        r = c.post("/api/bridge/apply", json={"token": "e2e-token", "onebot_port": 6199})
        rec("桥接写入两侧", r.status_code == 200, r.json().get("onebot_port"))
        r = c.get("/api/bridge/status")
        st = r.json()
        rec("桥接两侧均已配置", st["astrbot"]["configured"] and st["snowluma"]["configured"], st.get("hint"))
        rec("SnowLuma 指向 6199/ws", str(st["snowluma"].get("url", "")).endswith(":6199/ws"), st["snowluma"].get("url"))
        r = c.post("/api/bridge/verify")
        rec("连通性检查可执行", r.status_code == 200, r.json().get("probe", {}).get("detail"))

        # ---- 服务状态与启停保护
        r = c.get("/api/service/status")
        svcs = {s["name"]: s for s in r.json()["services"]}
        rec("服务状态含两个服务", set(svcs) == {"astrbot", "snowluma"}, list(svcs))
        r = c.post("/api/service/astrbot/start")
        rec("未安装时启动被拒绝", r.status_code in (400, 409, 424, 500), r.json().get("code") or r.status_code)
        r = c.post("/api/service/nope/start")
        rec("未知服务 404", r.status_code == 404, r.json().get("code"))

        # ---- v0.1.1：凭据三态 + 补救入口（FIX-04/05）
        r = c.get("/api/credential/snowluma")
        cap = r.json().get("capture", {})
        rec("凭据带 password_status", cap.get("password_status") in ("captured", "unknown", "user_managed"), cap.get("password_status"))
        hist = r.json().get("history", {})
        rec("SnowLuma 历史目录检测", "history" in hist, hist.get("history"))
        r = c.post("/api/credential/astrbot/recapture")
        rec("重新抓取密码可用", r.status_code == 200, r.json().get("state") or r.json().get("password_status"))
        r = c.post("/api/credential/astrbot/manual", json={"password": "manual-pwd-123"})
        rec("手动粘贴密码入库", r.status_code == 200 and r.json().get("password_status") in ("captured", "user_managed"), r.json().get("password_status"))
        r = c.post("/api/credential/astrbot/manual", json={"password": ""})
        rec("空密码被拒绝", r.status_code == 400 and r.json().get("code") == "invalid_body", r.json().get("code"))

        # ---- v0.1.1：设置高级项（FIX-02/03/07）
        r = c.post("/api/settings/network-mode", json={"mode": "host"})
        rec("切换 Docker 网络模式", r.status_code == 200 and r.json().get("network_mode") == "host", r.json().get("regenerated"))
        r = c.post("/api/settings/network-mode", json={"mode": "bridge"})
        rec("网络模式可切回 bridge", r.status_code == 200, r.json().get("network_mode"))
        r = c.put("/api/settings", json={"timeouts": {"service_ready": 3}})
        rec("超时越界被拒绝", r.status_code == 400, r.json().get("code"))
        r = c.put("/api/settings", json={"timeouts": {"service_ready": 200}})
        rec("超时可配置", r.status_code == 200 and r.json()["settings"]["timeouts"]["service_ready"] == 200)
        r = c.put("/api/settings", json={"lifecycle": {"shutdown_child_processes": False}})
        rec("退出连带停子进程可关闭", r.status_code == 200, r.json()["settings"]["lifecycle"]["shutdown_child_processes"])

        # ---- v0.1.1：桥接地址由控制器给出（FIX-02）
        r = c.get("/api/bridge/endpoint")
        ep = r.json()
        rec("桥接端点由 ServiceController 给出", r.status_code == 200 and bool(ep.get("url")), ep.get("url"))
        rec("端点带候选与策略", bool(ep.get("candidates")), len(ep.get("candidates") or []))

        # ---- v0.1.1：孤儿进程与残留清理（FIX-06/07）
        r = c.get("/api/service/orphans")
        rec("孤儿进程接口可用", r.status_code == 200 and "count" in r.json(), r.json().get("count"))
        r = c.post("/api/service/orphans/scan")
        rec("孤儿进程可重扫", r.status_code == 200, r.json().get("count"))
        r = c.post("/api/service/snowluma/kill-residual")
        rec("残留清理接口可用", r.status_code in (200, 409), r.json().get("code") or r.json().get("result"))

        # ---- SSE 日志流
        with c.stream("GET", "/api/service/console/log", params={"tail": 5}) as resp:
            rec("SSE 日志流可建立", resp.status_code == 200 and "text/event-stream" in resp.headers.get("content-type", ""),
                resp.headers.get("content-type"))
            got = b""
            t0 = time.time()
            for chunk in resp.iter_bytes():
                got += chunk
                if b"data:" in got or time.time() - t0 > 6:
                    break
            rec("SSE 有数据帧", b"data:" in got, got[:120])

    ok = all(x["ok"] for x in results)
    print(json.dumps({"total": len(results), "failed": [x["case"] for x in results if not x["ok"]], "ok": ok},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
