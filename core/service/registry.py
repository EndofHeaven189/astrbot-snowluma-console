"""运行态注册表（state\\pids.json）。

控制台重启后要能接管上一次拉起的进程，所以把「服务 → 主 pid + 启动时间 + 命令行」
落盘。读取时一定再用平台层验证存活，绝不盲信文件内容（FIX-06 / FIX-07）。

v0.1.1 起文件名从 runtime.json 改成 pids.json（需求增量的新增文件清单），
读到老文件会自动迁移，老用户的现场不会被丢掉。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from core.constants import STATE_PIDS
from core.platform import get_platform
from core.utils import atomic_write_json, now_iso, read_json

_LOCK = threading.RLock()

# 服务 → 命令行关键字：判断某个 pid 是不是这个服务拉起来的
SERVICE_KEYWORDS: Dict[str, List[str]] = {
    "astrbot": ["astrbot", "uvicorn", "cmd_config"],
    "snowluma": ["snowluma", "launcher.bat", "index.mjs", "onebot"],
}


def _dir() -> str:
    from core.paths import get_layout

    return get_layout().state


def _path() -> str:
    return os.path.join(_dir(), STATE_PIDS)


def _legacy_path() -> str:
    return os.path.join(_dir(), "runtime.json")


def _load() -> Dict[str, Any]:
    data = read_json(_path(), None)
    if isinstance(data, dict) and "entries" in data:
        entries = data.get("entries")
        return dict(entries) if isinstance(entries, dict) else {}
    if isinstance(data, dict):
        # 老格式（服务名直接做 key）
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    legacy = read_json(_legacy_path(), None)
    if isinstance(legacy, dict):
        migrated = {k: v for k, v in legacy.items() if isinstance(v, dict)}
        if migrated:
            _save(migrated)
        return migrated
    return {}


def _save(entries: Dict[str, Any]) -> None:
    atomic_write_json(_path(), {"version": 1, "updated_at": now_iso(), "entries": entries})


def record(service: str, payload: Dict[str, Any]) -> None:
    with _LOCK:
        entries = _load()
        entry = dict(payload)
        entry["service"] = service
        entry.setdefault("started_at", now_iso())
        entry["recorded_at"] = now_iso()
        entries[service] = entry
        _save(entries)


def forget(service: str) -> None:
    with _LOCK:
        entries = _load()
        if service in entries:
            entries.pop(service, None)
            _save(entries)


def get(service: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _load().get(service)


def all_entries() -> Dict[str, Any]:
    with _LOCK:
        return _load()


def _alive(pid: int) -> bool:
    adapter = get_platform()
    checker = getattr(adapter, "pid_alive", None)
    return bool(checker(pid)) if callable(checker) else False


def alive_record(service: str) -> Optional[Dict[str, Any]]:
    """返回仍然存活的记录，顺带清理掉过期记录。"""
    entry = get(service)
    if not entry:
        return None
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        forget(service)
        return None
    if not _alive(pid):
        forget(service)
        return None
    return entry


def orphan_entries() -> List[Dict[str, Any]]:
    """pids.json 里仍然存活的条目——控制台启动时它们都是「上一次没收拾干净」的孤儿。

    只认命令行能对上的：pid 会被系统复用，光看存活会把别人的进程当成自己的服务。
    """
    adapter = get_platform()
    result: List[Dict[str, Any]] = []
    changed = False
    with _LOCK:
        entries = _load()
        for service, entry in list(entries.items()):
            pid = entry.get("pid") if isinstance(entry, dict) else None
            if not isinstance(pid, int) or pid <= 0 or not _alive(pid):
                entries.pop(service, None)
                changed = True
                continue
            keywords = SERVICE_KEYWORDS.get(service, [service])
            line = adapter.process_command_line(pid) or ""
            if line and not adapter.command_matches(pid, keywords):
                entries.pop(service, None)
                changed = True
                continue
            result.append(
                {
                    "service": service,
                    "pid": pid,
                    "mode": entry.get("mode"),
                    "cwd": entry.get("cwd"),
                    "port": entry.get("port"),
                    "started_at": entry.get("started_at"),
                    "command": line or " ".join(entry.get("cmd") or []),
                    "matched": bool(line),
                }
            )
        if changed:
            _save(entries)
    return result


def drop(service: str) -> None:
    forget(service)
