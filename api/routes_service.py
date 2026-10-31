"""服务启停、状态、日志（需求 6.9 / 7）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from core.constants import SERVICE_ASTRBOT, SERVICE_SNOWLUMA
from core.credential import capture as credential_capture
from core.credential.snowluma import capture_from_logs, login_url
from core.errors import PreconditionError
from core.installer.jobs import INSTALLER_CHANNEL, get_registry
from core.loghub import get_loghub
from core.paths import get_layout
from core.service import lifecycle
from core.service.factory import get_controller
from core.settings import get_settings

from api.deps import guard_service

router = APIRouter(prefix="/api/service", tags=["service"])

CONSOLE_CHANNEL = "console"


def _controller(service: str):
    return get_controller(service)


def _gather() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for name in (SERVICE_ASTRBOT, SERVICE_SNOWLUMA):
        try:
            results.append(_controller(name).status().to_dict())
        except Exception as exc:  # 单个服务异常不影响整体状态
            results.append(
                {
                    "name": name,
                    "running": False,
                    "installed": False,
                    "error": str(exc),
                }
            )
    return results


@router.get("/status")
def status() -> Dict[str, Any]:
    registry = get_registry()
    active = registry.active()
    snowluma_ui = None
    try:
        controller = _controller(SERVICE_SNOWLUMA)
        st = controller.status()
        snowluma_ui = login_url() if st.installed else None
    except Exception:
        snowluma_ui = None
    return {
        "services": _gather(),
        "install_job": active.to_dict() if active else None,
        "snowluma_login_url": snowluma_ui,
        "deploy_mode": get_settings().astrbot_deploy_mode(),
        "capture": {
            SERVICE_ASTRBOT: credential_capture.status(SERVICE_ASTRBOT),
            SERVICE_SNOWLUMA: credential_capture.status(SERVICE_SNOWLUMA),
        },
        "orphans": lifecycle.orphan_report(),
    }


async def _lifecycle(
    service: str,
    action: str,
    wait: bool,
    force: bool = False,
    timeout: float = 300.0,
) -> Dict[str, Any]:
    controller = _controller(service)
    if action == "start":
        st = await controller.start(wait=wait, timeout=timeout)
    elif action == "stop":
        st = await controller.stop(force=force)
    else:
        st = await controller.restart(wait=wait, timeout=timeout)

    payload: Dict[str, Any] = {"service": service, "action": action, "status": st.to_dict()}
    if service == SERVICE_SNOWLUMA and action in ("start", "restart"):
        # 兜底：某些版本忽略环境变量仍会打印一次性密码。
        # FIX-AV：read_log 在 Docker 路线下会同步跑 docker logs（超时 45 秒），
        # 所以放到线程里，别卡住事件循环。
        try:
            lines = await asyncio.to_thread(controller.read_log, 200)
            pwd = capture_from_logs(lines)
            payload["login_url"] = login_url(pwd)
        except Exception:
            payload["login_url"] = None
    state_text = "运行中" if st.running else "已停止"
    get_loghub().append(CONSOLE_CHANNEL, f"[{action}] {service} → {state_text}")
    return payload


@router.post("/{service}/start")
async def start(
    service: str,
    wait: bool = Query(False),
    timeout: float = Query(300.0),
    acknowledge: bool = Query(False),
):
    guard_service(service)
    if service == SERVICE_SNOWLUMA:
        # FIX-05：历史数据目录不会重新生成初始密码，启动前必须让用户明确选择
        from core.installer.snowluma import require_no_history

        require_no_history(get_layout(), acknowledge=acknowledge, action="启动")
    return await _lifecycle(service, "start", wait, timeout=timeout)


@router.post("/{service}/stop")
async def stop(service: str, force: bool = Query(False)):
    guard_service(service)
    return await _lifecycle(service, "stop", False, force)


@router.post("/{service}/restart")
async def restart(
    service: str,
    wait: bool = Query(True),
    timeout: float = Query(300.0),
    acknowledge: bool = Query(False),
):
    guard_service(service)
    if service == SERVICE_SNOWLUMA:
        from core.installer.snowluma import require_no_history

        require_no_history(get_layout(), acknowledge=acknowledge, action="重启")
    return await _lifecycle(service, "restart", wait, timeout=timeout)


@router.get("/orphans")
def orphans() -> Dict[str, Any]:
    """FIX-07：上一次控制台异常退出留下的子进程。"""
    items = lifecycle.orphan_report()
    return {
        "orphans": items,
        "count": len(items),
        "hint": "这些进程上次没能随控制台一起退出，仍在占用端口。可以「接管」继续用，或「结束」后重新启动。"
        if items
        else "",
    }


@router.post("/orphans/scan")
def rescan_orphans() -> Dict[str, Any]:
    items = lifecycle.scan_orphans()
    return {"orphans": items, "count": len(items)}


@router.post("/orphans/{service}/resolve")
async def resolve_orphan(service: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    guard_service(service)
    action = str((payload or {}).get("action") or "kill")
    if action not in ("kill", "adopt"):
        raise PreconditionError("action 只能是 kill 或 adopt", code="invalid_body")
    return await asyncio.to_thread(lifecycle.resolve_orphan, service, action)


@router.post("/{service}/kill-residual")
async def kill_residual(service: str) -> Dict[str, Any]:
    """FIX-06：停止后端口仍被占（残留子进程）时的一键清理。"""
    guard_service(service)
    controller = _controller(service)
    cleaner = getattr(controller, "kill_residual", None)
    if not callable(cleaner):
        raise PreconditionError("当前部署形态不支持残留清理", code="not_supported")
    result = await asyncio.to_thread(cleaner)
    get_loghub().append(CONSOLE_CHANNEL, f"[kill-residual] {service} → {result}")
    return {"service": service, "result": result, "status": _controller(service).status().to_dict()}


@router.get("/{service}/log/recent")
def recent_log(service: str, tail: int = Query(400, ge=0, le=5000)) -> Dict[str, Any]:
    if service in (INSTALLER_CHANNEL, CONSOLE_CHANNEL):
        lines = get_loghub().snapshot(service, tail)
        return {"service": service, "lines": lines, "file": get_loghub().file_path(service)}
    guard_service(service)
    controller = _controller(service)
    lines = controller.read_log(tail=tail) if tail else []
    if not lines:
        lines = get_loghub().snapshot(service, tail)
    return {"service": service, "lines": lines, "file": get_loghub().file_path(service)}


@router.get("/{service}/log")
async def stream_log(service: str, tail: int = Query(200, ge=0, le=2000)) -> StreamingResponse:
    if service not in (INSTALLER_CHANNEL, CONSOLE_CHANNEL):
        guard_service(service)
    hub = get_loghub()

    async def events() -> AsyncIterator[bytes]:
        async for item in hub.stream(service, tail=tail):
            if item.get("type") == "ping":
                yield b": ping\n\n"
                continue
            payload = json.dumps(item, ensure_ascii=False)
            yield ("data: " + payload + "\n\n").encode("utf-8")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{service}/ready")
async def ready(service: str, timeout: float = Query(60.0)) -> Dict[str, Any]:
    guard_service(service)
    controller = _controller(service)
    try:
        ok = await asyncio.wait_for(controller.wait_ready(timeout=timeout), timeout=timeout + 5)
    except asyncio.TimeoutError:
        ok = False
    return {"service": service, "ready": bool(ok), "url": controller.ready_url()}
