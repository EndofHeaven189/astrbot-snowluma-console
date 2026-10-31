"""验收自检（对应需求文档第 9 节的 6 条硬性验收 + v0.1.2 新增断言）。

用法：python scripts/acceptance_check.py
退出码 0 表示全绿。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from typing import Any, Dict, List

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
WEB_SRC = os.path.join(ROOT, "web", "src")

# FIX-BD：旧正则多转义了一层，C:\Users\foo 实际不命中。
DRIVE_PATTERN = re.compile(r"[A-Za-z]:[\\/]{1,2}(?:Users|Program|Windows)")
# FIX-BJ：前端不得硬编码服务端口（设置里的端口才是真源）。
FRONTEND_PORT_PATTERN = re.compile(r"(?:127\.0\.0\.1|localhost):\d{2,5}")


def walk_code(exts=None):
    exts = exts or SCAN_EXT
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue  # 临时产物（.dom_*.html 等）
            if os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(base, name)


# ---------------------------------------------------------------- 验收 6：禁止写法
def check_no_forbidden() -> List[Dict[str, Any]]:
    """验收 6：全局无 platform.system() / 盘符字面量 / os.system(。"""
    patterns = {
        "platform.system(": re.compile(r"platform\.system\s*\("),
        "os.system(": re.compile(r"os\.system\s*\("),
        "盘符字面量": DRIVE_PATTERN,
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


def check_frontend_port_literals() -> List[Dict[str, Any]]:
    """FIX-BJ：前端 .vue/.ts 里不得出现 127.0.0.1:6185 这类硬编码端口。"""
    hits: List[Dict[str, Any]] = []
    for base, dirs, files in os.walk(WEB_SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if os.path.splitext(name)[1].lower() not in (".vue", ".ts"):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for match in FRONTEND_PORT_PATTERN.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(
                    {
                        "file": os.path.relpath(path, ROOT),
                        "line": line,
                        "kind": "前端端口字面量",
                        "text": match.group(0),
                    }
                )
    return hits


# ---------------------------------------------------------------- 验收 6：协议记录
def check_eula_record() -> Dict[str, Any]:
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


# ---------------------------------------------------------------- 验收 4：真源
def check_single_source_of_truth() -> Dict[str, Any]:
    """验收 4：服务管理/配置读写/凭据重置三处读同一份 deploy_mode。"""
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


# ---------------------------------------------------------------- 验收 1/2：路由
def check_routes() -> Dict[str, Any]:
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


# ---------------------------------------------------------------- 验收 9：抽象层
def check_abstraction_layer() -> Dict[str, Any]:
    """验收 9：check_abstraction.py 零告警。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import check_abstraction

    result = check_abstraction.scan()
    return {"ok": result["ok"], "warnings": result["warnings"], "scanned": result["scanned"]}


# ---------------------------------------------------------------- 验收 3：单端口
def check_dist() -> Dict[str, Any]:
    """验收 3：单端口托管（前端构建产物存在）。"""
    index = os.path.join(ROOT, "web", "dist", "index.html")
    return {
        "exists": os.path.isfile(index),
        "path": index,
        "size": os.path.getsize(index) if os.path.isfile(index) else 0,
    }


# ---------------------------------------------------------------- 临时根目录
def _temp_layout(prefix: str):
    from core.paths import Layout
    from core.settings import Settings

    tmp = tempfile.mkdtemp(prefix=prefix)
    layout = Layout(tmp)
    layout.ensure()
    settings = Settings(layout).load(force=True)
    return layout, settings


def _interface_fields(name: str) -> List[str]:
    """从 web/src/api/types.ts 里解析某个 interface 的字段名。"""
    path = os.path.join(ROOT, "web", "src", "api", "types.ts")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    match = re.search(r"export interface\s+" + name + r"\s*\{(.*?)\n\}", text, re.S)
    if not match:
        return []
    fields: List[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "/*", "*")):
            continue
        found = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", line)
        if found:
            fields.append(found.group(1))
    return fields


def check_detect_contract() -> Dict[str, Any]:
    """契约 1（FIX-E / FIX-BE / FIX-F）：检测返回体与 types.ts 一致，桥接端点带包装。"""
    from core.detector import env as envmod
    from core.platform import PortState

    problems: List[str] = []
    layout, settings = _temp_layout("console-detect-")

    original_scan = envmod.scan_ports
    original_specs = envmod._detect_specs
    envmod.scan_ports = lambda ports: [PortState(port=p, in_use=False) for p in ports]
    envmod._detect_specs = lambda layout_, settings_, route: [
        ("ports", "端口占用", "common", lambda: envmod._check_ports(settings_), 5.0)
    ]
    try:
        payload = asyncio.run(envmod.detect_environment(force=True, layout=layout, settings=settings))
    finally:
        envmod.scan_ports = original_scan
        envmod._detect_specs = original_specs

    check_fields = set(_interface_fields("CheckItem"))
    detect_fields = set(_interface_fields("DetectResult"))
    if not check_fields or not detect_fields:
        problems.append("无法从 web/src/api/types.ts 解析 CheckItem / DetectResult")

    checks = payload.get("checks") or []
    if not checks:
        problems.append("detect_environment() 没有返回 checks")
    for item in checks:
        extra = sorted(set(item) - check_fields)
        if extra:
            problems.append(f"检测项多出 types.ts 未声明的字段：{extra}")
        if "state" in item:
            problems.append("检测项仍在用 state 字段（已统一为 status）")
        if item.get("status") not in ("ok", "warn", "fail", "pending"):
            problems.append(f"检测项 status 取值非法：{item.get('status')!r}")
    extra_payload = sorted(set(payload) - detect_fields)
    if extra_payload:
        problems.append(f"检测返回体多出 types.ts 未声明的字段：{extra_payload}")

    # FIX-F / FIX-AH：/api/bridge/endpoint 顶层必须包一层，且 reachable 是顶层字段
    try:
        from api.routes_bridge import endpoint as endpoint_route

        for refresh in (False, True):
            body = endpoint_route(refresh=refresh)
            if "endpoint" not in body:
                problems.append("/api/bridge/endpoint 返回体缺少 endpoint 包装")
                continue
            ep = body.get("endpoint") or {}
            if "reachable" not in ep:
                problems.append("endpoint 缺少 reachable 字段（FIX-AH）")
            elif ep.get("reachable") is not None and not isinstance(ep.get("reachable"), bool):
                problems.append("endpoint.reachable 既不是布尔也不是 null")
    except Exception as exc:  # noqa: BLE001 - 依赖本机 Docker 时不该让验收崩溃
        problems.append(f"/api/bridge/endpoint 调用失败：{exc!r}")

    return {"ok": not problems, "problems": problems, "check_fields": len(check_fields)}


def check_compose_and_venv() -> Dict[str, Any]:
    """语义冒烟 2（FIX-B / FIX-C）：compose 真落盘；不存在的 venv 探测返回 None。"""
    from core.installer.astrbot import write_compose
    from core.platform import get_platform

    problems: List[str] = []
    layout, settings = _temp_layout("console-compose-")
    settings.update({"bridge": {"onebot_port": 6199}})
    path = write_compose(layout, settings)
    if not os.path.isfile(path):
        problems.append("write_compose() 没有把文件写到 layout.astrbot_compose_file")
    else:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "services:" not in text or "astrbot:" not in text:
            problems.append("compose 内容缺少 services:/astrbot:")
    exe = get_platform().venv_executable(os.path.join(layout.root, "no-such-venv"), "python")
    if exe is not None:
        problems.append(f"venv_executable() 对不存在的 venv 返回 {exe!r}，应为 None")
    return {"ok": not problems, "problems": problems}


def check_port_detection() -> Dict[str, Any]:
    """端口检测 3 + 控制台端口一致性 5（FIX-D / FIX-AI）。"""
    from core.detector import env as envmod
    from core.platform import PortState

    problems: List[str] = []
    _layout, settings = _temp_layout("console-ports-")
    settings.update({"console": {"port": 8899}, "bridge": {"onebot_port": 7000}})

    original = envmod.scan_ports
    envmod.scan_ports = lambda ports: [
        PortState(port=p, in_use=(p in (8899, 7000)), pid=4242, process="mockproc") for p in ports
    ]
    try:
        items = envmod._check_ports(settings)
    finally:
        envmod.scan_ports = original

    by_id = {item["id"]: item for item in items}
    own = by_id.get("port_8899")
    if own is None:
        problems.append("console.port=8899 时没有出现 port_8899 检测项（FIX-AI）")
    elif own.get("status") != "ok" or "由本控制台占用" not in str(own.get("detail")):
        problems.append(f"控制台自身端口应判 ok/由本控制台占用，实际 {own.get('status')}/{own.get('detail')}")
    occupied = by_id.get("port_7000")
    if occupied is None:
        problems.append("没有出现 port_7000 检测项")
    elif occupied.get("status") != "warn":
        problems.append(f"被占用的 7000 端口应判 warn，实际 {occupied.get('status')}")
    elif "mockproc(4242)" not in str(occupied.get("detail")):
        problems.append("占用详情里没有进程名与 PID")
    return {"ok": not problems, "problems": problems}


def check_settings_guard() -> Dict[str, Any]:
    """真源写入 4（FIX-AF / FIX-BA）：点号键与受保护键一律 400。"""
    from api.routes_settings import update_settings
    from core.errors import ConsoleError

    problems: List[str] = []
    payloads = [
        {"astrbot": {"deploy_mode": "docker"}},
        {"astrbot.deploy_mode": "docker"},
        {"console.host": "0.0.0.0"},
        {"console.port": 99999},
        {"timeouts.service_ready": 0.001},
    ]
    for payload in payloads:
        try:
            update_settings(payload)
        except ConsoleError as exc:
            if exc.http_status != 400:
                problems.append(f"{payload} 返回 {exc.http_status}，期望 400")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{payload} 抛出非 ConsoleError：{exc!r}")
        else:
            problems.append(f"{payload} 竟然写成功了")
    return {"ok": not problems, "problems": problems}


def check_compose_port_consistency() -> Dict[str, Any]:
    """compose / 配置端口一致性 7（FIX-BB）：容器内外与 cmd_config 同端口。"""
    from core.bridge.manager import apply_bridge
    from core.installer.astrbot import _compose_text
    from core.utils import atomic_write_json

    problems: List[str] = []
    layout, settings = _temp_layout("console-port-")
    settings.update({"bridge": {"onebot_port": 7000}})
    os.makedirs(layout.astrbot_data, exist_ok=True)
    atomic_write_json(layout.astrbot_config_file, {"platform": []})
    apply_bridge(token="test-token", settings=settings, layout=layout)

    with open(layout.astrbot_config_file, "r", encoding="utf-8-sig") as fh:
        config = json.load(fh)
    written = None
    for item in config.get("platform") or []:
        if isinstance(item, dict) and item.get("type") == "aiocqhttp":
            written = item.get("ws_reverse_port")
    text = _compose_text(layout, settings)
    if written != 7000:
        problems.append(f"apply_bridge 写入的 ws_reverse_port 是 {written}，期望 7000")
    if '"7000:7000"' not in text:
        problems.append('compose 没有按同一端口映射（期望 "7000:7000"）')
    return {"ok": not problems, "problems": problems}


def check_password_length() -> Dict[str, Any]:
    """密码位数 6（FIX-AO）：generate_password(16) 必须正好 16 位。"""
    from core.credential.reset import generate_password

    problems: List[str] = []
    for length in (16, 12):
        password = generate_password(length)
        if len(password) != length:
            problems.append(f"generate_password({length}) 返回 {len(password)} 位")
    return {"ok": not problems, "problems": problems}


def check_backup_restore_guard() -> Dict[str, Any]:
    """还原路径约束 7（FIX-AN）：布局外的 _meta.file 必须被拒绝。"""
    from core.config.store import restore_backup
    from core.errors import BackupError
    from core.utils import atomic_write_json

    problems: List[str] = []
    layout, settings = _temp_layout("console-restore-")
    outside = os.path.join(tempfile.gettempdir(), "console-restore-should-not-exist.json")
    if os.path.exists(outside):
        os.remove(outside)
    name = f"guard_{int(time.time())}.json"
    atomic_write_json(
        os.path.join(layout.backups, name),
        {"_meta": {"service": "snowluma", "file": outside, "reason": "test"}, "data": {"x": 1}},
    )
    try:
        restore_backup(name, layout=layout, settings=settings)
    except BackupError:
        pass
    except Exception as exc:  # noqa: BLE001
        problems.append(f"期望 BackupError，实际 {exc!r}")
    else:
        problems.append("布局外的 _meta.file 竟然被还原了（FIX-AN）")
    if os.path.exists(outside):
        problems.append("还原写到了布局之外")
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------- 判定分支
def _p_list(prefix: str):
    return lambda value: [f"{prefix}（{len(value)} 处）"] if value else []


def _p_single_source(value: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for key, entry in value.items():
        if not isinstance(entry, dict):
            problems.append(f"{key} 检查失败：{entry}")
        elif not entry.get("reads_deploy_mode"):
            problems.append(f"{key} 没有读 settings.astrbot_deploy_mode()（deploy_mode 真源）")
    return problems


def _p_status(key_prefix: str):
    def handler(value: Dict[str, Any]) -> List[str]:
        if value.get("ok"):
            return []
        return [f"{key_prefix}：" + "；".join(value.get("problems") or ["未通过"])]

    return handler


PROBLEM_HANDLERS = {
    "forbidden_hits": _p_list("出现禁止的写法"),
    "frontend_port_literals": _p_list("前端出现硬编码端口"),
    "eula": lambda v: [] if not v.get("accepted") or v.get("has_timestamp") else ["协议记录缺少时间戳"],
    "deploy_mode_single_source": _p_single_source,
    "routes": lambda v: [f"缺少路由：{v['missing']}"] if v.get("missing") else [],
    "web_dist": lambda v: [] if v.get("exists") else ["web/dist 不存在，需先 cd web && npm run build"],
    "abstraction": lambda v: []
    if v.get("ok")
    else [f"平台抽象层自检有 {v.get('warnings')} 条告警（scripts/check_abstraction.py）"],
    "detect_contract": _p_status("前后端检测契约不一致"),
    "compose_and_venv": _p_status("compose / venv 冒烟失败"),
    "port_detection": _p_status("端口检测不合格"),
    "settings_guard": _p_status("设置校验未生效"),
    "compose_port_consistency": _p_status("compose 与桥接端口不一致"),
    "password_length": _p_status("密码位数不合格"),
    "backup_restore_guard": _p_status("备份还原越界"),
    # 元自检自身也要有判定分支
    "_meta": lambda v: []
    if v.get("ok")
    else [f"以下检查项没有判定分支（元自检）：{v.get('unhandled')}"],
}


def main() -> int:
    report: Dict[str, Any] = {
        "forbidden_hits": check_no_forbidden(),
        "frontend_port_literals": check_frontend_port_literals(),
        "eula": check_eula_record(),
        "deploy_mode_single_source": check_single_source_of_truth(),
        "routes": check_routes(),
        "web_dist": check_dist(),
        "abstraction": check_abstraction_layer(),
        "detect_contract": check_detect_contract(),
        "compose_and_venv": check_compose_and_venv(),
        "port_detection": check_port_detection(),
        "settings_guard": check_settings_guard(),
        "compose_port_consistency": check_compose_port_consistency(),
        "password_length": check_password_length(),
        "backup_restore_guard": check_backup_restore_guard(),
    }

    # 元自检（FIX-BC）：report 里每个键都必须有判定分支，防止再加「死检查」
    unhandled = [key for key in report if key not in PROBLEM_HANDLERS]
    report["_meta"] = {"ok": not unhandled, "unhandled": unhandled}

    problems: List[str] = []
    for key, value in report.items():
        problems.extend(PROBLEM_HANDLERS[key](value))

    report["ok"] = not problems
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
