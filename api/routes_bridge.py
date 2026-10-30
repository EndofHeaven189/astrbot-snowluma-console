"""桥接配置（需求 6.8）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter

from core.bridge.manager import apply_bridge, bridge_endpoint, bridge_status, verify_bridge
from core.constants import DEPLOY_DOCKER
from core.errors import PreconditionError
from core.paths import get_layout
from core.service.factory import get_controller
from core.settings import get_settings

from api.deps import body_dict, parse_port

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


@router.get("/status")
def status() -> Dict[str, Any]:
    return bridge_status()


@router.post("/apply")
def apply(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    token = data.get("token")
    port = data.get("onebot_port")
    result = apply_bridge(
        token=str(token) if token else None,
        onebot_port=parse_port(port) if port not in (None, "") else None,
    )
    result["status"] = bridge_status()
    result["restart_required"] = True
    return result


@router.get("/endpoint")
def endpoint(refresh: bool = False) -> Dict[str, Any]:
    """FIX-02：当前路线下 SnowLuma 应该连到哪一个地址。"""
    settings = get_settings()
    layout = get_layout()
    data = bridge_endpoint(settings=settings, layout=layout)
    if settings.is_docker():
        controller = get_controller("astrbot", settings=settings, layout=layout)
        resolver = getattr(controller, "resolve_bridge_endpoint", None)
        if callable(resolver) and refresh:
            try:
                data["resolved"] = resolver()
            except Exception as exc:
                data["resolved"] = {"reachable": False, "reason": "probe_error", "error": str(exc)}
    return data


@router.post("/fallback")
def fallback(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX-02 退路 2：容器连不上宿主机时，一键把网络模式切成 host 并重写 compose。"""
    data = body_dict(payload)
    mode = str(data.get("mode") or "host").strip().lower()
    if mode != "host":
        raise PreconditionError(
            "目前只提供 host 网络这一条自动退路", code="unsupported_fallback", http_status=400
        )
    settings = get_settings()
    if settings.astrbot_deploy_mode() != DEPLOY_DOCKER:
        raise PreconditionError(
            "该退路仅适用于 Docker 路线", code="wrong_route", http_status=400
        )
    from core.installer.astrbot import write_compose

    layout = get_layout()
    layout.ensure()
    settings.update({"astrbot": {"docker_network_mode": "host"}})
    settings.save()
    compose = write_compose(layout, settings)
    result = {
        "network_mode": "host",
        "compose": compose,
        "restart_required": True,
        "hint": "已切到 host 网络并重写 compose。请到「服务」页重启 AstrBot，容器会直接使用宿主机网络，桥接地址改为 127.0.0.1。",
    }
    result["endpoint"] = bridge_endpoint(settings=settings, layout=layout)
    return result


@router.post("/verify")
async def verify() -> Dict[str, Any]:
    return await verify_bridge()
