"""安装与路线切换（需求 6.2 / 6.3 / 6.4 / 5.4）。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter

from core.constants import DEPLOY_DOCKER, DEPLOY_UV, SERVICE_ASTRBOT, SERVICE_SNOWLUMA
from core.credential.eula import acceptance_status, record_acceptance
from core.errors import ConsoleError, PreconditionError
from core.installer.astrbot import install_astrbot
from core.installer.jobs import get_registry, run_job
from core.installer.snowluma import VARIANTS, archive_config, install_snowluma, require_no_history
from core.paths import get_layout
from core.platform import get_platform
from core.service.factory import (
    get_controller,
    snowluma_installed,
    venv_astrbot_exe,
)
from core.settings import get_settings

from api.deps import body_dict, guard_route, parse_bool, parse_port

router = APIRouter(prefix="/api/install", tags=["install"])

_TASKS: Set["asyncio.Task[Any]"] = set()


def _spawn(job, coro) -> None:
    task = asyncio.create_task(run_job(job, coro))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


@router.post("/astrbot")
async def install_astrbot_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    route = guard_route(str(data.get("mode") or data.get("route") or "").strip().lower())
    upgrade = parse_bool(data.get("upgrade"), False)

    settings = get_settings()
    layout = get_layout()
    settings.set_astrbot_deploy_mode(route)
    if data.get("dashboard_port") is not None:
        settings.update({"astrbot": {"dashboard_port": parse_port(data["dashboard_port"])}})
    if data.get("onebot_port") is not None:
        settings.update({"astrbot": {"onebot_port": parse_port(data["onebot_port"])}})
    settings.save()

    if route == DEPLOY_DOCKER:
        adapter = get_platform()
        if not adapter.which("docker"):
            raise PreconditionError(
                "未检测到 Docker，无法使用 Docker 路线",
                hint="请先安装并启动 Docker Desktop，或改用 uv/pip 路线",
            )

    registry = get_registry()
    job = registry.create("astrbot", f"安装 AstrBot（{route}）")
    _spawn(job, install_astrbot(job, route=route, layout=layout, settings=settings, upgrade=upgrade))
    return {"job": job.to_dict(), "mode": route}


@router.post("/astrbot/switch-mode")
async def switch_mode(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    target = guard_route(str(data.get("mode") or data.get("route") or "").strip().lower())
    settings = get_settings()
    layout = get_layout()
    current = settings.astrbot_deploy_mode()
    if current == target:
        return {
            "changed": False,
            "mode": current,
            "installed": bool(venv_astrbot_exe(layout)) if target == DEPLOY_UV else None,
            "hint": "当前已是该路线",
        }

    stopped = False
    if current:
        controller = get_controller(SERVICE_ASTRBOT, settings=settings, layout=layout)
        if controller.status().running:
            await controller.stop()
            stopped = True

    settings.set_astrbot_deploy_mode(target)
    settings.save()

    adapter = get_platform()
    missing: List[str] = []
    if target == DEPLOY_DOCKER:
        if not adapter.which("docker"):
            missing.append("docker")
    else:
        if not adapter.which("python") and not os.path.exists(layout.astrbot_venv):
            missing.append("python")

    installed = venv_astrbot_exe(layout) is not None if target == DEPLOY_UV else None
    return {
        "changed": True,
        "mode": target,
        "previous_mode": current,
        "stopped": stopped,
        "installed": installed,
        "missing": missing,
        "instances_kept": layout.instances,
        "needs_install": bool(missing) or (target == DEPLOY_UV and not installed),
        "hint": "instances 目录未改动，数据与插件都保留；新路线可能需要重新执行安装。",
    }


@router.post("/snowluma")
async def install_snowluma_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = body_dict(payload)
    layout = get_layout()
    variant = str(data.get("variant") or "lite").strip().lower()
    if variant not in VARIANTS:
        raise ConsoleError(
            f"未知安装包类型：{variant}",
            code="invalid_variant",
            http_status=400,
            hint="可用值：lite / full",
        )
    accept_eula = parse_bool(data.get("accept_eula"), False)
    accept_privacy = parse_bool(data.get("accept_privacy"), False)
    manual = str(data.get("mode") or "checkbox") == "manual"

    if not manual and not (accept_eula and accept_privacy):
        raise PreconditionError(
            "需要同时勾选 EULA 与隐私政策才能安装 SnowLuma",
            hint="两个协议必须都勾选；如无法勾选，请走人工确认流程",
            detail={"accept_eula": accept_eula, "accept_privacy": accept_privacy},
        )

    # FIX-05：历史数据目录必须前置确认，不能静默继续
    acknowledge = parse_bool(data.get("acknowledge_history"), False)
    fresh_data = parse_bool(data.get("fresh_data"), False)
    archived = None
    if fresh_data:
        archived = archive_config(layout)
    else:
        require_no_history(layout, acknowledge=acknowledge, action="安装")

    version = None
    try:
        from core.detector.env import latest_release

        release = latest_release("SnowLuma/SnowLuma", layout=layout)
        version = (release or {}).get("tag") or (release or {}).get("version")
    except Exception:
        version = None
    if accept_eula and accept_privacy:
        record_acceptance(
            version=version or "unknown",
            mode="manual" if manual else "checkbox",
            actor="console",
            layout=layout,
        )

    # 记下这次选的安装包类型，设置页与状态接口显示的就是实际装的那一个
    settings = get_settings()
    settings.update({"snowluma": {"variant": variant}})
    settings.save()

    registry = get_registry()
    job = registry.create("snowluma", f"安装 SnowLuma（{variant}）")
    _spawn(
        job,
        install_snowluma(
            job,
            variant=variant,
            force=parse_bool(data.get("force"), False),
            accept_eula=True,
            eula_mode="manual" if manual else "checkbox",
            layout=layout,
        ),
    )
    return {"job": job.to_dict(), "variant": variant, "archived_config": archived}


@router.get("/jobs")
def list_jobs(limit: int = 10) -> Dict[str, Any]:
    registry = get_registry()
    active = registry.active()
    return {
        "active": active.to_dict() if active else None,
        "jobs": registry.recent(limit=max(1, min(limit, 50))),
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = get_registry().get(job_id)
    if not job:
        raise ConsoleError("任务不存在", code="job_not_found", http_status=404, detail={"job": job_id})
    return job.to_dict()


@router.get("/state")
def install_state() -> Dict[str, Any]:
    layout = get_layout()
    settings = get_settings()
    return {
        "astrbot": {
            "deploy_mode": settings.astrbot_deploy_mode(),
            "uv_installed": venv_astrbot_exe(layout) is not None,
            "instance_dir": layout.astrbot_instance,
            "data_dir": layout.astrbot_data,
            "compose_file": layout.astrbot_compose_file,
        },
        "snowluma": {
            "installed": snowluma_installed(layout),
            "app_dir": layout.snowluma_app,
            "config_dir": layout.snowluma_config_dir,
            "eula": acceptance_status(layout),
        },
        "variants": {k: v for k, v in VARIANTS.items()},
    }
