"""服务启停适配层。"""

from core.service.base import ServiceController, ServiceStatus
from core.service.factory import get_controller

__all__ = ["ServiceController", "ServiceStatus", "get_controller"]
