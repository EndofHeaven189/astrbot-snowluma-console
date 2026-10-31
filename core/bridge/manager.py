"""桥接管理（需求 6.8）。

一侧：AstrBot cmd_config.json 里的 aiocqhttp 适配器开启反向 WS（6199 + token）。
另一侧：SnowLuma config/onebot.json 的 networks.wsClients 增加一个指向
ws://127.0.0.1:6199/ws 的客户端，accessToken 与 AstrBot 一致。

两处都必须走 ConfigStore，保证备份与原子写一致。
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, Dict, List, Optional

from core.config.store import ConfigStore, get_config_store
from core.constants import DEPLOY_DOCKER, LOOPBACK, SERVICE_ASTRBOT, SERVICE_SNOWLUMA
from core.errors import BridgeError
from core.paths import Layout, get_layout
from core.settings import Settings, get_settings
from core.utils import now_iso

ASTRBOT_PLATFORM_KEYS = ("platform", "platforms", "platform_settings")


def _platform_list(config: Dict[str, Any]) -> Optional[List[Any]]:
    for key in ASTRBOT_PLATFORM_KEYS:
        value = config.get(key)
        if isinstance(value, list):
            return value
    return None


def _find_aiocqhttp(items: List[Any]) -> Optional[Dict[str, Any]]:
    for item in items:
        if isinstance(item, dict) and str(item.get("type", "")).lower() == "aiocqhttp":
            return item
    return None


def _ensure_aiocqhttp(config: Dict[str, Any], token: str, port: int) -> Dict[str, Any]:
    items = _platform_list(config)
    if items is None:
        # FIX-N：platform 键存在但不是 list（例如被手工改成了 dict）时，
        # 以前直接抛「配置结构无法识别」。空的 dict 视为「还没有平台」，换成 []；
        # 非空的就明确拒绝，并把可操作指引放进 detail / hint，而不是让用户猜。
        present = [(key, config.get(key)) for key in ASTRBOT_PLATFORM_KEYS if key in config]
        if not present:
            for key in ASTRBOT_PLATFORM_KEYS:
                if key not in config:
                    config[key] = []
                    items = config[key]
                    break
        else:
            key, value = present[0]
            if isinstance(value, dict) and not value:
                config[key] = []
                items = config[key]
            else:
                raise BridgeError(
                    f"AstrBot 配置里的 {key} 不是数组，无法安全写入 aiocqhttp",
                    hint=(
                        f"请到 AstrBot 面板 → 配置 → 平台适配器里手动添加一个 aiocqhttp（OneBot v11），"
                        f"或先把 cmd_config.json 的 {key} 改回数组再点「一键对齐」。"
                    ),
                    detail={"key": key, "type": type(value).__name__, "value": str(value)[:300]},
                )
    if items is None:
        raise BridgeError("AstrBot 配置结构无法识别（找不到 platform 列表）")
    item = _find_aiocqhttp(items)
    if item is None:
        item = {
            "id": "default",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "0.0.0.0",
            "ws_reverse_port": port,
            "ws_reverse_token": token,
        }
        items.append(item)
    else:
        item["enable"] = True
        item["ws_reverse_host"] = item.get("ws_reverse_host") or "0.0.0.0"
        item["ws_reverse_port"] = port
        item["ws_reverse_token"] = token
        item.setdefault("type", "aiocqhttp")
        item.setdefault("id", "default")
    config.setdefault("dashboard", {})
    if isinstance(config["dashboard"], dict):
        config["dashboard"].setdefault("enable", True)
    return item


def _snowluma_networks(onebot: Dict[str, Any]) -> Dict[str, Any]:
    networks = onebot.get("networks")
    if not isinstance(networks, dict):
        networks = {}
        onebot["networks"] = networks
    networks.setdefault("httpServers", [])
    networks.setdefault("httpClients", [])
    networks.setdefault("wsServers", [])
    clients = networks.get("wsClients")
    if not isinstance(clients, list):
        networks["wsClients"] = []
    return networks


def _ensure_ws_client(
    onebot: Dict[str, Any], token: str, port: int, host: str = LOOPBACK
) -> Dict[str, Any]:
    """写入 SnowLuma 的反向 WS 客户端。

    目标地址由控制器给出（FIX-02）：Docker 路线要写 host.docker.internal 或宿主机 IP，
    写死 127.0.0.1 在容器里指向容器自己，连不上。
    """
    networks = _snowluma_networks(onebot)
    clients: List[Any] = networks["wsClients"]
    target = f"ws://{host}:{port}/ws"
    for item in clients:
        if isinstance(item, dict) and str(item.get("name", "")) in ("astrbot", "console", "AstrBot"):
            item.update(
                {
                    "name": "astrbot",
                    "url": target,
                    "accessToken": token,
                    "enabled": True,
                    "role": item.get("role") or "Universal",
                    "messageFormat": item.get("messageFormat") or "array",
                    "reportSelfMessage": bool(item.get("reportSelfMessage", False)),
                }
            )
            return item
    entry = {
        "name": "astrbot",
        "url": target,
        "accessToken": token,
        "enabled": True,
        "role": "Universal",
        "messageFormat": "array",
        "reportSelfMessage": False,
    }
    clients.append(entry)
    return entry


# ---------------------------------------------------------------- 桥接端点
def bridge_endpoint(
    settings: Optional[Settings] = None, layout: Optional[Layout] = None
) -> Dict[str, Any]:
    """由控制器给出「另一侧该连哪个地址」（FIX-02，业务层不写死 IP）。"""
    from core.service.factory import get_controller

    settings = settings or get_settings()
    layout = layout or get_layout()
    controller = get_controller(SERVICE_ASTRBOT, settings, layout)
    return controller.get_bridge_endpoint("container" if settings.is_docker() else "host")


def _resolve_endpoint_for_apply(settings: Settings, layout: Layout) -> Dict[str, Any]:
    """Docker 路线在写入配置前先在容器里探一遍；容器没跑就跳过探测。"""
    from core.service.factory import get_controller

    controller = get_controller(SERVICE_ASTRBOT, settings, layout)
    if settings.is_docker() and hasattr(controller, "resolve_bridge_endpoint"):
        probe = controller.resolve_bridge_endpoint()
        if probe.get("reason") == "container_not_running":
            endpoint = controller.get_bridge_endpoint("container")
            endpoint["probe"] = "skipped"
            endpoint["note"] = "容器尚未启动，已按默认网关别名写入；启动后可用「连通性校验」确认。"
            return endpoint
        if not probe.get("reachable"):
            raise BridgeError(
                "容器连不上宿主机，桥接无法生效",
                hint=(
                    "容器内探测 host.docker.internal 与宿主机局域网 IP 都失败："
                    "请到「设置 → 高级」把容器网络切换为 network_mode: host，重新生成 compose 后重试。"
                ),
                detail=probe,
            )
        probe["probe"] = "ok"
        return probe
    endpoint = controller.get_bridge_endpoint("host")
    endpoint["probe"] = "not-required"
    return endpoint


# ---------------------------------------------------------------- 读取现状
def _inspect_astrbot(store: ConfigStore) -> Dict[str, Any]:
    try:
        payload = store.read(SERVICE_ASTRBOT, "cmd_config")
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "error": str(exc)[:200]}
    data = payload.get("data") or {}
    items = _platform_list(data if isinstance(data, dict) else {}) or []
    item = _find_aiocqhttp(items)
    dashboard = data.get("dashboard") if isinstance(data, dict) else {}
    return {
        "exists": payload.get("exists", False),
        "path": payload.get("path"),
        "configured": bool(item),
        "enabled": bool(item.get("enable")) if item else False,
        "port": item.get("ws_reverse_port") if item else None,
        "host": item.get("ws_reverse_host") if item else None,
        "token_set": bool(item.get("ws_reverse_token")) if item else False,
        "dashboard_port": dashboard.get("port") if isinstance(dashboard, dict) else None,
    }


def _inspect_snowluma(store: ConfigStore) -> Dict[str, Any]:
    try:
        payload = store.read(SERVICE_SNOWLUMA, "onebot")
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "error": str(exc)[:200]}
    data = payload.get("data") or {}
    networks = (data.get("networks") or {}) if isinstance(data, dict) else {}
    clients = networks.get("wsClients") or []
    target = None
    for item in clients:
        if isinstance(item, dict) and str(item.get("name", "")) in ("astrbot", "console", "AstrBot"):
            target = item
            break
    servers = networks.get("wsServers") or []
    http_servers = networks.get("httpServers") or []
    return {
        "exists": payload.get("exists", False),
        "path": payload.get("path"),
        "configured": bool(target),
        "enabled": bool(target.get("enabled")) if target else False,
        "url": target.get("url") if target else None,
        "token_set": bool(target.get("accessToken")) if target else False,
        "ws_servers": len(servers),
        "http_servers": len(http_servers),
    }


def bridge_status(
    *,
    settings: Optional[Settings] = None,
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    settings = settings or get_settings()
    layout = layout or get_layout()
    store = get_config_store(layout, settings)
    astrbot = _inspect_astrbot(store)
    snowluma = _inspect_snowluma(store)
    # FIX-H：OneBot 端口只认 settings.onebot_port()（bridge.onebot_port 优先）
    port = settings.onebot_port()
    token_set = bool(settings.get("bridge.token"))
    consistent = bool(
        astrbot.get("configured")
        and snowluma.get("configured")
        and astrbot.get("enabled")
        and snowluma.get("enabled")
    )
    endpoint: Dict[str, Any]
    try:
        endpoint = bridge_endpoint(settings, layout)
    except Exception as exc:  # noqa: BLE001
        endpoint = {"error": str(exc)[:200], "url": None}
    return {
        "onebot_port": port,
        "token_set": token_set,
        "astrbot": astrbot,
        "snowluma": snowluma,
        "consistent": consistent,
        "endpoint": endpoint,
        "deploy_mode": settings.astrbot_deploy_mode(),
        "hint": None
        if consistent
        else "两侧配置尚未对齐，请点「一键对齐」后重启两个服务。",
        "checked_at": now_iso(),
    }


# ---------------------------------------------------------------- 写入
def apply_bridge(
    *,
    token: Optional[str] = None,
    onebot_port: Optional[int] = None,
    settings: Optional[Settings] = None,
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    settings = settings or get_settings()
    layout = layout or get_layout()
    store = get_config_store(layout, settings)

    port = int(onebot_port or settings.onebot_port())
    if port <= 0 or port > 65535:
        raise BridgeError("OneBot 端口不合法", detail={"port": port})
    token = (token if token is not None else settings.get("bridge.token")) or ""
    generated = False
    if not token:
        token = secrets.token_urlsafe(24)
        generated = True

    endpoint = _resolve_endpoint_for_apply(settings, layout)
    results: Dict[str, Any] = {
        "onebot_port": port,
        "token_generated": generated,
        "endpoint": endpoint,
    }

    astrbot_payload = store.read(SERVICE_ASTRBOT, "cmd_config")
    astrbot_data = astrbot_payload.get("data")
    if not isinstance(astrbot_data, dict):
        raise BridgeError(
            "AstrBot 配置不存在或不是对象，请先启动一次 AstrBot 生成默认配置",
            hint="若尚未安装 AstrBot，请先在「安装」页完成安装并启动一次。",
            detail={"path": astrbot_payload.get("path")},
        )
    _ensure_aiocqhttp(astrbot_data, token, port)
    results["astrbot"] = store.write(
        SERVICE_ASTRBOT, "cmd_config", astrbot_data, reason="bridge-apply"
    )

    snowluma_payload = store.read(SERVICE_SNOWLUMA, "onebot")
    snowluma_data = snowluma_payload.get("data")
    if not isinstance(snowluma_data, dict):
        snowluma_data = {}
    _ensure_ws_client(snowluma_data, token, port, str(endpoint.get("host") or LOOPBACK))
    results["snowluma"] = store.write(
        SERVICE_SNOWLUMA, "onebot", snowluma_data, reason="bridge-apply"
    )

    # 注意：Settings.update 只接受嵌套结构，带点号的键会被拒绝（FIX-AF）。
    settings.update({"bridge": {"token": token, "onebot_port": port}})
    settings.save()
    results["token"] = token
    results["restart_required"] = True
    results["applied_at"] = now_iso()
    return results


# ---------------------------------------------------------------- 连通性
async def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "detail": f"{host}:{port} 可连接"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{host}:{port} 连接失败：{exc}"}


async def verify_bridge(
    *,
    settings: Optional[Settings] = None,
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    """连通性校验（FIX-02）：按部署形态给不同的失败文案与排查项。"""
    settings = settings or get_settings()
    layout = layout or get_layout()
    port = settings.onebot_port()
    endpoint = bridge_endpoint(settings, layout)
    mode = settings.astrbot_deploy_mode()
    probe = await _tcp_probe(LOOPBACK, port)
    status = bridge_status(settings=settings, layout=layout)
    hints: List[str] = []
    checks: List[Dict[str, Any]] = []

    checks.append(
        {
            "id": "astrbot_ws_listening",
            "label": f"宿主机 {LOOPBACK}:{port} 可连接",
            "ok": bool(probe["ok"]),
            "detail": probe["detail"],
        }
    )

    if not probe["ok"]:
        if mode == DEPLOY_DOCKER:
            hints.append(
                "Docker 路线常见原因是容器 ↔ 宿主机网络：容器里的 127.0.0.1 是容器自己。"
                "请在「桥接」页点「一键切换退路」，或在「设置 → 高级」切到 network_mode: host 后重新生成 compose。"
            )
            hints.append("同时确认 Docker Desktop 的 WSL2 端口映射把 6199 映射出来了（docker ps 里端口列应为 0.0.0.0:6199->6199）。")
        elif mode == "uv":
            hints.append("确认 AstrBot 已启动，且日志里出现 aiocqhttp 反向 WS 端口监听信息。")
        else:
            hints.append("尚未选择 AstrBot 部署路线，请先到「安装」页完成安装。")

    # Docker 路线额外做一次容器内探测：证明容器确实能连回宿主机
    if mode == DEPLOY_DOCKER:
        try:
            from core.service.factory import get_controller

            controller = get_controller(SERVICE_ASTRBOT, settings, layout)
            resolver = getattr(controller, "resolve_bridge_endpoint", None)
            if callable(resolver):
                inside = resolver()
                checks.append(
                    {
                        "id": "container_to_host",
                        "label": f"容器内可连接 {endpoint.get('url')}",
                        "ok": bool(inside.get("reachable")),
                        "detail": inside.get("note") or inside.get("reason") or "",
                        "endpoint": inside,
                    }
                )
                if not inside.get("reachable") and inside.get("reason") != "container_not_running":
                    hints.append("容器内探测失败：可在「设置 → 高级」切换容器网络退路后重新生成 compose。")
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "id": "container_to_host",
                    "label": "容器内连通性探测",
                    "ok": False,
                    "detail": f"探测未能执行：{exc}",
                }
            )

    snowluma_url = (status.get("snowluma") or {}).get("url")
    checks.append(
        {
            "id": "snowluma_target_matches",
            "label": "SnowLuma 目标地址与控制器给出的一致",
            "ok": bool(snowluma_url and endpoint.get("url") and snowluma_url == endpoint.get("url")),
            "detail": f"配置：{snowluma_url or '未配置'} / 建议：{endpoint.get('url')}",
        }
    )
    if snowluma_url and endpoint.get("url") and snowluma_url != endpoint.get("url"):
        hints.append("两侧地址不一致，点「一键对齐」会按当前部署形态重写 SnowLuma 的目标地址。")

    checks.append(
        {
            "id": "config_aligned",
            "label": "两侧配置已对齐且启用",
            "ok": bool(status["consistent"]),
            "detail": "配置一致" if status["consistent"] else "两侧配置尚未对齐",
        }
    )
    if not status["consistent"]:
        hints.append("两侧配置尚未对齐，请先执行一键对齐并重启服务。")

    ok = all(item["ok"] for item in checks if item["id"] != "container_to_host")
    return {
        "ok": bool(ok and status["consistent"]),
        "probe": probe,
        "endpoint": endpoint,
        "checks": checks,
        "status": status,
        "hints": hints,
        "checked_at": now_iso(),
    }
