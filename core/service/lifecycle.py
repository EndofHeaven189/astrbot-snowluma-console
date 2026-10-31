"""控制台退出时的子进程回收（FIX-07），以及启动时的孤儿自检。

场景：用户直接叉掉 start.bat 窗口 / 任务管理器结束 AstrBotConsole.exe / 控制台崩溃。
这些情况下 uvicorn 的 shutdown 事件不一定会跑，子进程会变成孤儿并继续占用端口，
下次启动就会撞上「端口已被占用」。两道保险：

1. FastAPI shutdown + atexit 双重注册（正常退出、Ctrl+C 都能触发）；
2. 启动时自检 pids.json：发现上次遗留的存活进程就在 UI 上提示「接管 / 结束」，
   并且在用户处理之前不允许重复拉起第二个实例。
"""

from __future__ import annotations

import atexit
import threading
import time
from typing import Any, Dict, List

from core.constants import STOP_GRACE_SECONDS
from core.platform import get_platform
from core.service import registry

_REAP_LOCK = threading.Lock()
_REAPED = False
_ORPHANS: List[Dict[str, Any]] = []
_ORPHAN_LOCK = threading.Lock()


def orphan_report() -> List[Dict[str, Any]]:
    """启动时扫描的孤儿进程（供 /api/service/orphans 读取）。"""
    with _ORPHAN_LOCK:
        return [dict(item) for item in _ORPHANS]


def clear_orphans() -> None:
    with _ORPHAN_LOCK:
        _ORPHANS.clear()


def scan_orphans() -> List[Dict[str, Any]]:
    """启动自检：把这次失控留下的进程找出来，只提示不自动杀。"""
    try:
        items = registry.orphan_entries()
    except Exception:
        items = []
    with _ORPHAN_LOCK:
        _ORPHANS.clear()
        _ORPHANS.extend(items)
    return [dict(item) for item in _ORPHANS]


def resolve_orphan(service: str, action: str = "kill") -> Dict[str, Any]:
    """处理一个孤儿：kill=结束它，adopt=接管（保留进程与注册表记录）。"""
    entry = registry.get(service)
    if not entry:
        raise ValueError(f"没有 {service} 的遗留记录")
    pid = entry.get("pid")
    result: Dict[str, Any] = {"service": service, "action": action, "pid": pid}
    if action == "adopt":
        result["adopted"] = True
    elif action == "kill":
        if isinstance(pid, int) and pid > 0:
            adapter = get_platform()
            try:
                adapter.graceful_stop(pid)
            except Exception:
                pass
            deadline = time.time() + _grace_seconds()
            while time.time() < deadline:
                if not _alive(pid):
                    break
                time.sleep(0.4)
            if _alive(pid):
                adapter.kill_tree(pid, force=True)
            result["killed"] = True
        registry.forget(service)
    else:
        raise ValueError(f"未知的处理方式：{action}")
    with _ORPHAN_LOCK:
        _ORPHANS[:] = [item for item in _ORPHANS if item.get("service") != service]
    return result


def _alive(pid: int) -> bool:
    adapter = get_platform()
    checker = getattr(adapter, "pid_alive", None)
    return bool(checker(pid)) if callable(checker) else False


def _grace_seconds() -> float:
    """FIX-I：读 settings.timeouts.stop_grace，atexit 场景下读不到就回落常量。"""
    try:
        from core.settings import get_settings

        return float(get_settings().timeout("stop_grace"))
    except Exception:  # noqa: BLE001 - 退出路径上任何异常都不能再抛
        return float(STOP_GRACE_SECONDS)


def reap_children(reason: str = "shutdown") -> List[Dict[str, Any]]:
    """按 pids.json 逐个优雅停止子进程，超时强杀。返回处理过的服务列表。"""
    global _REAPED
    from core.settings import get_settings

    with _REAP_LOCK:
        if _REAPED:
            return []
        _REAPED = True
    try:
        from core.loghub import get_loghub

        hub = get_loghub()
    except Exception:
        hub = None
    if not get_settings().shutdown_child_processes():
        if hub:
            hub.append("console", f"[{reason}] 已关闭「退出时结束子进程」，保留服务继续运行")
        return []
    adapter = get_platform()
    handled: List[Dict[str, Any]] = []
    for service, entry in list(registry.all_entries().items()):
        pid = entry.get("pid") if isinstance(entry, dict) else None
        if not isinstance(pid, int) or pid <= 0 or not _alive(pid):
            registry.forget(service)
            continue
        if hub:
            hub.append("console", f"[{reason}] 结束 {service}（pid={pid}）")
        try:
            adapter.graceful_stop(pid)
        except Exception:
            pass
        deadline = time.time() + _grace_seconds()
        while time.time() < deadline:
            if not _alive(pid):
                break
            time.sleep(0.4)
        forced = False
        if _alive(pid):
            forced = True
            try:
                adapter.kill_tree(pid, force=True)
            except Exception:
                pass
        registry.forget(service)
        handled.append({"service": service, "pid": pid, "forced": forced})
    return handled


def install_exit_hook() -> None:
    """atexit 兜底：uvicorn 没走到 shutdown（例如 sys.exit / 未捕获崩溃）时也回收。"""
    atexit.register(reap_children, "atexit")
