"""服务控制器接口（需求 5.4）。

同一套接口，三种部署形态各一个实现：
    DockerComposeController  AstrBot 路线 B（容器）
    ProcessController        AstrBot 路线 A（venv 原生进程）与 SnowLuma（launcher.bat）

业务代码（API / 安装器 / 凭据 / 配置）只认这个接口，不认路线。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.constants import LOOPBACK


@dataclass
class ServiceStatus:
    name: str
    installed: bool = False
    running: bool = False
    version: Optional[str] = None
    deploy_mode: Optional[str] = None
    pid: Optional[int] = None
    port: Optional[int] = None
    url: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "installed": self.installed,
            "running": self.running,
            "version": self.version,
            "deploy_mode": self.deploy_mode,
            "pid": self.pid,
            "port": self.port,
            "url": self.url,
            "detail": self.detail,
        }


class ServiceController(ABC):
    """启停 + 状态 + 日志读取的统一抽象。"""

    name: str = ""
    deploy_mode: str = ""

    # ---------------------------------------------------------------- 状态
    @abstractmethod
    def status(self) -> ServiceStatus:
        """当前状态（不产生副作用）。"""

    @abstractmethod
    def is_installed(self) -> bool:
        """是否已安装（只看文件，不看运行状态）。"""

    # ---------------------------------------------------------------- 生命周期
    @abstractmethod
    async def start(self, wait: bool = False, timeout: float = 300.0) -> ServiceStatus:
        """启动服务；wait=True 时轮询就绪。"""

    @abstractmethod
    async def stop(self, force: bool = False) -> ServiceStatus:
        """停止服务（先优雅，超时强杀）。"""

    async def restart(self, wait: bool = False, timeout: float = 300.0) -> ServiceStatus:
        await self.stop()
        return await self.start(wait=wait, timeout=timeout)

    # ---------------------------------------------------------------- 日志
    @abstractmethod
    def read_log(self, tail: int = 400) -> List[str]:
        """读取服务日志（凭据抓取与日志页共用）。"""

    @abstractmethod
    async def wait_ready(self, timeout: float = 120.0) -> bool:
        """等待服务就绪（面板端口可访问）。"""

    # ---------------------------------------------------------------- 扩展
    def ready_url(self) -> Optional[str]:
        return None

    def get_bridge_endpoint(self, dial_from: str = "host") -> Dict[str, Any]:
        """SnowLuma 该往哪儿连（FIX-02）。

        业务代码只拿这个返回值拼 onebot 的 ws 地址，不许自己写死 127.0.0.1。
        默认实现：本机回环 + 服务端口。
        """
        port = getattr(self, "_port", None) or getattr(self, "port", None)
        host = LOOPBACK
        url = f"ws://{host}:{port}/ws" if port else None
        return {
            "route": getattr(self, "deploy_mode", ""),
            "host": host,
            "port": port,
            "url": url,
            "strategy": "loopback",
            "candidates": [{"host": host, "reachable": None}],
            "note": "服务与本机在同一网络命名空间，直接用回环地址。",
        }

    def log_source(self) -> str:
        return getattr(self, "deploy_mode", "") or "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return self.status().to_dict()
