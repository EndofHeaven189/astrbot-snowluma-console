"""控制台设置（需求 5.4 的唯一真源）。

deploy_mode 只允许通过 /api/install/astrbot 或 /api/install/astrbot/switch-mode 修改，
这里刻意不接受 deploy_mode 字段，避免多个入口写同一份状态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from core.credential.eula import (
    acceptance_status,
    is_accepted,
    record_acceptance,
    revoke_acceptance,
)
from core.constants import DEPLOY_DOCKER, LOOPBACK
from core.errors import ConsoleError, SettingsError
from core.paths import get_layout, reset_layout
from core.settings import get_settings

from api.deps import body_dict, parse_bool, parse_port

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROTECTED_KEYS = ("astrbot.deploy_mode",)

NETWORK_MODES = ("bridge", "host")
# 超时上下限（秒）：下限防止用户把它设成 0 导致必定失败，上限防止睡死。
TIMEOUT_BOUNDS = (5, 86400)


def _reject_protected(data: Dict[str, Any]) -> None:
    if "deploy_mode" in data:
        raise SettingsError(
            "deploy_mode 只能通过安装/切换路线接口修改",
            hint="请调用 POST /api/install/astrbot 或 /api/install/astrbot/switch-mode",
        )
    astrbot = data.get("astrbot")
    if isinstance(astrbot, dict) and "deploy_mode" in astrbot:
        raise SettingsError(
            "deploy_mode 只能通过安装/切换路线接口修改",
            hint="请调用 POST /api/install/astrbot 或 /api/install/astrbot/switch-mode",
        )


@router.get("")
def read_settings() -> Dict[str, Any]:
    settings = get_settings()
    layout = get_layout()
    return {
        "settings": settings.snapshot(),
        "defaults": True,
        "file": layout.settings_file,
        "root": layout.root,
        "eula": acceptance_status(layout),
    }


@router.put("")
def update_settings(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    _reject_protected(data)
    settings = get_settings()

    if "console" in data and isinstance(data["console"], dict):
        console = data["console"]
        if "port" in console:
            console["port"] = parse_port(console["port"])
        if "host" in console:
            host = str(console["host"]).strip()
            if host not in (LOOPBACK, "localhost", "::1"):
                raise SettingsError(
                    "控制台只允许监听本机回环地址",
                    hint="可选值：127.0.0.1 / localhost / ::1",
                )
            console["host"] = host

    for section, keys in (
        ("astrbot", ("dashboard_port", "onebot_port")),
        ("snowluma", ("webui_port", "http_port", "ws_port")),
        ("bridge", ("onebot_port",)),
    ):
        block = data.get(section)
        if isinstance(block, dict):
            for key in keys:
                if key in block:
                    block[key] = parse_port(block[key])

    astrbot = data.get("astrbot")
    if isinstance(astrbot, dict) and "docker_network_mode" in astrbot:
        mode = str(astrbot["docker_network_mode"] or "").strip().lower()
        if mode not in NETWORK_MODES:
            raise SettingsError(
                "astrbot.docker_network_mode 只能是 bridge 或 host",
                hint="bridge 用 host.docker.internal；host 直接用 127.0.0.1",
            )
        astrbot["docker_network_mode"] = mode

    timeouts = data.get("timeouts")
    if isinstance(timeouts, dict):
        low, high = TIMEOUT_BOUNDS
        cleaned: Dict[str, Any] = {}
        for name, value in timeouts.items():
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                raise SettingsError(f"超时 {name} 必须是数字（秒）", code="invalid_timeout")
            if not (low <= seconds <= high):
                raise SettingsError(
                    f"超时 {name} 超出范围（{low}~{high} 秒）", code="invalid_timeout"
                )
            cleaned[str(name)] = int(seconds) if seconds.is_integer() else seconds
        data["timeouts"] = cleaned

    lifecycle = data.get("lifecycle")
    if isinstance(lifecycle, dict) and "shutdown_child_processes" in lifecycle:
        lifecycle["shutdown_child_processes"] = parse_bool(lifecycle["shutdown_child_processes"], True)

    merged = settings.update(data)
    settings.save()
    return {"settings": merged, "saved": True}


@router.post("/network-mode")
def switch_network_mode(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX-02 退路 2：切换 Docker 容器网络模式（bridge ↔ host）。

    host 网络下容器直接共享宿主机网络栈，桥接地址退回 127.0.0.1；
    bridge 网络下靠 extra_hosts 的 host.docker.internal 或宿主机局域网 IP。
    切换后重新渲染 compose 文件，需要用户重新 up -d 才生效。
    """
    data = body_dict(payload)
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in NETWORK_MODES:
        raise SettingsError(
            "network_mode 只能是 bridge 或 host",
            hint="bridge：默认隔离网络，靠 host.docker.internal 连宿主机；host：共用一个网络栈",
        )
    settings = get_settings()
    settings.update({"astrbot": {"docker_network_mode": mode}})
    settings.save()
    compose = None
    regenerated = False
    if settings.astrbot_deploy_mode() == DEPLOY_DOCKER:
        from core.installer.astrbot import write_compose

        layout = get_layout()
        layout.ensure()
        compose = write_compose(layout, settings)
        regenerated = True
    return {
        "network_mode": mode,
        "compose": compose,
        "regenerated": regenerated,
        "restart_required": regenerated,
        "hint": "compose 已按新网络模式重写，请到「服务」页重启 AstrBot 让容器生效"
        if regenerated
        else "当前不是 Docker 路线，保存后仅影响以后生成的 compose",
    }


@router.get("/eula")
def read_eula() -> Dict[str, Any]:
    return acceptance_status(get_layout())


@router.post("/eula")
def accept_eula(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    version = str(data.get("version") or "unknown")
    mode = "manual" if str(data.get("mode") or "") == "manual" else "checkbox"
    accept_eula_flag = parse_bool(data.get("eula"), True)
    accept_privacy_flag = parse_bool(data.get("privacy"), True)
    if mode == "checkbox" and not (accept_eula_flag and accept_privacy_flag):
        raise ConsoleError(
            "必须同时勾选 EULA 与隐私政策",
            code="eula_incomplete",
            http_status=400,
            hint="两个协议都勾选后才能继续",
        )
    if mode == "manual":
        accept_eula_flag = accept_privacy_flag = True
    record = record_acceptance(version=version, mode=mode, actor="console")
    return {"acceptance": record, "status": acceptance_status(get_layout())}


@router.delete("/eula")
def revoke() -> Dict[str, Any]:
    revoke_acceptance(get_layout())
    return {"status": acceptance_status(get_layout())}


@router.post("/root")
def change_root(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """切换数据根目录（不会迁移数据，只切换指向）。"""
    data = body_dict(payload)
    root = str(data.get("root") or "").strip()
    if not root:
        raise SettingsError("请提供新的数据目录")
    layout = reset_layout(root)
    layout.ensure()
    settings = get_settings()
    settings.set_root_dir(layout.root)
    settings.save()
    return {"root": layout.root, "dirs": layout.as_dict(), "hint": "旧目录数据未迁移，如需保留请手动拷贝 instances 目录"}
