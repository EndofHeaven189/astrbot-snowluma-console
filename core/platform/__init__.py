"""平台适配器工厂。

业务代码用 get_platform() 拿适配器，永远不要直接 import windows/linux 子模块。
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Optional

from core.platform.base import PlatformAdapter, PortState, ProcessHandle

_LOCK = threading.RLock()
_ADAPTER: Optional[PlatformAdapter] = None


def build_adapter() -> PlatformAdapter:
    if os.name == "nt":
        from core.platform.windows import WindowsAdapter

        return WindowsAdapter()
    from core.platform.linux import LinuxAdapter

    return LinuxAdapter()


def get_platform() -> PlatformAdapter:
    global _ADAPTER
    with _LOCK:
        if _ADAPTER is None:
            _ADAPTER = build_adapter()
        return _ADAPTER


def scan_ports(ports) -> "list[PortState]":
    """批量探测端口占用情况。"""
    adapter = get_platform()
    result: "list[PortState]" = []
    for port in ports:
        try:
            found = adapter.find_process_by_port(port)
        except Exception:
            found = None
        if found:
            result.append(PortState(port=port, in_use=True, pid=found[0], process=found[1]))
        else:
            result.append(PortState(port=port, in_use=False))
    return result


def free_port(adapter: PlatformAdapter, start: int, limit: int = 50) -> int:
    """从 start 开始找第一个空闲端口。"""
    for port in range(start, start + limit):
        if adapter.check_port(port) is None:
            return port
    return start


__all__ = [
    "PlatformAdapter",
    "PortState",
    "ProcessHandle",
    "build_adapter",
    "get_platform",
    "scan_ports",
    "free_port",
    "shutil",
]
