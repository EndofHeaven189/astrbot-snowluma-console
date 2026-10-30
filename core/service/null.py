"""未选择部署路线时的占位控制器。

存在的意义：状态接口永远可调用，路线未选时返回「未安装」而不是 500。
"""

from __future__ import annotations

from typing import List, Optional

from core.errors import PreconditionError
from core.service.base import ServiceController, ServiceStatus


class UnconfiguredController(ServiceController):
    deploy_mode = "unconfigured"

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            name=self.name,
            installed=False,
            running=False,
            deploy_mode=None,
            detail={"reason": self.reason},
        )

    def is_installed(self) -> bool:
        return False

    async def start(self, wait: bool = False, timeout: float = 300.0) -> ServiceStatus:
        raise PreconditionError(self.reason, hint="请先在安装向导中选择部署路线。")

    async def stop(self, force: bool = False) -> ServiceStatus:
        raise PreconditionError(self.reason, hint="该服务尚未配置。")

    def read_log(self, tail: int = 400) -> List[str]:
        return []

    def ready_url(self) -> Optional[str]:
        return None

    async def wait_ready(self, timeout: float = 120.0) -> bool:
        return False
