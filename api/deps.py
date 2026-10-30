"""路由共用的依赖与小工具。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from core.constants import DEPLOY_DOCKER, DEPLOY_UV, SERVICE_ASTRBOT, SERVICE_SNOWLUMA
from core.errors import ConsoleError
from core.paths import Layout, get_layout
from core.settings import Settings, get_settings

SERVICES = (SERVICE_ASTRBOT, SERVICE_SNOWLUMA)


def ctx() -> "tuple[Layout, Settings]":
    return get_layout(), get_settings()


LAYOUT: Layout = get_layout()
SETTINGS: Settings = get_settings()


def guard_service(service: str) -> str:
    if service not in SERVICES:
        raise ConsoleError(
            f"未知服务：{service}",
            code="unknown_service",
            http_status=404,
            hint="可用值：astrbot / snowluma",
            detail={"service": service},
        )
    return service


def guard_route(route: str) -> str:
    if route not in (DEPLOY_UV, DEPLOY_DOCKER):
        raise ConsoleError(
            f"未知部署路线：{route}",
            code="unknown_route",
            http_status=400,
            hint="可用值：uv / docker",
            detail={"route": route},
        )
    return route


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def parse_port(value: Any, default: Optional[int] = None) -> int:
    if value in (None, ""):
        if default is None:
            raise ConsoleError("端口不能为空", code="invalid_port", http_status=400)
        return default
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ConsoleError(
            f"端口必须是数字：{value!r}", code="invalid_port", http_status=400
        )
    if not (1 <= port <= 65535):
        raise ConsoleError(f"端口超出范围：{port}", code="invalid_port", http_status=400)
    return port


def body_dict(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConsoleError("请求体必须是 JSON 对象", code="invalid_body", http_status=400)
    return payload
