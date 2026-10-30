"""验收自检（对应需求文档第 9 节的 6 条硬性验收）。

用法：python scripts/acceptance_check.py
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 只扫业务代码。state/ 是运行期安装树（含第三方 site-packages 与下载的 zip），
# apps/ instances/ downloads/ backups/ logs/ 同理；dist 是前端构建产物。
SKIP_DIRS = {
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    ".git",
    "apps",
    "instances",
    "downloads",
    "backups",
    "logs",
    "state",
    "build",  # PyInstaller 工作目录（packaging/build）
}
SCAN_EXT = {".py", ".bat", ".ts", ".vue", ".js", ".css", ".html", ".json", ".md"}
# 禁止写法针对可执行业务代码；.md 是文档正文（会引用这些名字讲解规则），不参与匹配。
FORBIDDEN_EXT = SCAN_EXT - {".md"}


def walk_code(exts=None):
    exts = exts or SCAN_EXT
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue  # 临时产物（.dom_*.html 等）
            if os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(base, name)


def check_no_forbidden():
    """验收 6：全局无 platform.system() / 盘符字面量 / os.system(。"""
    patterns = {
        "platform.system(": re.compile(r"platform\.system\s*\("),
        "os.system(": re.compile(r"os\.system\s*\("),
        "盘符字面量": re.compile(r"[A-Za-z]:\\\\Users|C:\\\\Program"),
    }
    hits = []
    # 两个自检脚本里出现这些名字是「规则本身」，不是业务写法
    self_paths = {
        os.path.abspath(__file__),
        os.path.abspath(os.path.join(ROOT, "scripts", "check_abstraction.py")),
    }
    for path in walk_code(FORBIDDEN_EXT):
        if os.path.abspath(path) in self_paths:
            continue  # 检测脚本里是规则本身，不算业务代码
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for label, pattern in patterns.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append({"file": os.path.relpath(path, ROOT), "line": line, "kind": label})
    return hits


def check_eula_record():
    """验收 6：协议记录可追溯（字段齐全）。"""
    from core.credential.eula import acceptance_status
    from core.paths import get_layout

    status = acceptance_status(get_layout())
    return {
        "path": status.get("path"),
        "accepted": status.get("accepted"),
        "has_timestamp": bool(status.get("accepted_at")),
        "has_version": status.get("version") is not None,
        "mode": status.get("mode"),
        "env_flags": status.get("env_flags"),
    }


def check_single_source_of_truth():
    """验收 4：服务管理/配置读写/凭据重置三处读同一份 deploy_mode。"""
    import glob

    files = {
        "service": os.path.join(ROOT, "core", "service", "factory.py"),
        "config": os.path.join(ROOT, "core", "config", "store.py"),
        "reset": os.path.join(ROOT, "core", "credential", "reset.py"),
    }
    result = {}
    for key, path in files.items():
        if not os.path.isfile(path):
            result[key] = "missing"
            continue
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        result[key] = {
            "reads_deploy_mode": "astrbot_deploy_mode()" in text,
            "imports_settings": "settings" in text,
        }
    return result


def check_routes():
    """验收 1/2：API 路由齐全。"""
    from main import app

    # 新版 FastAPI 的 app.routes 里是惰性的 _IncludedRouter，用 OpenAPI 描述拿真实路径
    routes = sorted(app.openapi().get("paths", {}).keys())
    routes = [p for p in routes if p.startswith("/api")]
    required = [
        "/api/env/detect",
        "/api/env/install",
        "/api/install/astrbot",
        "/api/install/astrbot/switch-mode",
        "/api/service/status",
        "/api/service/{service}/start",
        "/api/service/{service}/stop",
        "/api/service/{service}/restart",
        "/api/service/{service}/log",
        "/api/config/{service}",
        "/api/credential/{service}",
        "/api/credential/{service}/reset",
        "/api/bridge/apply",
        "/api/bridge/status",
        "/api/bridge/verify",
        "/api/bridge/endpoint",
        "/api/bridge/fallback",
        "/api/settings",
        "/api/settings/network-mode",
        # v0.1.1 新增
        "/api/service/orphans",
        "/api/service/orphans/scan",
        "/api/service/orphans/{service}/resolve",
        "/api/service/{service}/kill-residual",
        "/api/credential/{service}/recapture",
        "/api/credential/{service}/manual",
    ]
    missing = [r for r in required if r not in routes]
    return {"total": len(routes), "missing": missing}


def check_abstraction_layer():
    """验收 9：check_abstraction.py 零告警。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import check_abstraction

    result = check_abstraction.scan()
    return {"ok": result["ok"], "warnings": result["warnings"], "scanned": result["scanned"]}


def check_dist():
    """验收 3：单端口托管（前端构建产物存在）。"""
    index = os.path.join(ROOT, "web", "dist", "index.html")
    return {
        "exists": os.path.isfile(index),
        "path": index,
        "size": os.path.getsize(index) if os.path.isfile(index) else 0,
    }


def main() -> int:
    report = {
        "forbidden_hits": check_no_forbidden(),
        "eula": check_eula_record(),
        "deploy_mode_single_source": check_single_source_of_truth(),
        "routes": check_routes(),
        "web_dist": check_dist(),
        "abstraction": check_abstraction_layer(),
    }
    problems = []
    if report["forbidden_hits"]:
        problems.append(f"出现 {len(report['forbidden_hits'])} 处禁止的写法")
    if not report["eula"].get("has_timestamp") and report["eula"].get("accepted"):
        problems.append("协议记录缺少时间戳")
    if report["routes"]["missing"]:
        problems.append(f"缺少路由：{report['routes']['missing']}")
    if not report["abstraction"]["ok"]:
        problems.append(f"平台抽象层自检有 {report['abstraction']['warnings']} 条告警（scripts/check_abstraction.py）")
    if not report["web_dist"]["exists"]:
        problems.append("web/dist 不存在，需先 cd web && npm run build")

    report["ok"] = not problems
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())