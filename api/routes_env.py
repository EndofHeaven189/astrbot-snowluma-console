"""环境检测与依赖准备（需求 6.1）。

/api/env/detect 只读，重活都在 core.detector.env。
/api/env/install 只处理控制台能自动完成的部分（uv），
Python / Node / Docker 需要人工安装，这里返回明确指引而不是假装成功。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from core.detector.env import detect_environment
from core.errors import ConsoleError
from core.paths import get_layout
from core.platform import get_platform
from core.settings import get_settings

router = APIRouter(prefix="/api/env", tags=["env"])

# FIX-AJ：SnowLuma lite 的 engines 是 ^24.22.1 || >=26.12.1，这里必须给满足下限的版本，
# 以前给的是 v24.11.0，装完仍然低于 MIN_NODE_FOR_LITE，页面提示与下载链接自相矛盾。
NODE_DOWNLOAD = "https://nodejs.org/dist/v24.22.1/node-v24.22.1-x64.msi"
DOCKER_DOWNLOAD = "https://www.docker.com/products/docker-desktop/"
PYTHON_DOWNLOAD = "https://www.python.org/downloads/windows/"

MANUAL_HINTS = {
    "python": f"AstrBot 需要 Python 3.12+（PyPI requires_python），安装时勾选 Add to PATH：{PYTHON_DOWNLOAD}",
    "node": f"SnowLuma 需要 Node.js ≥ 24.22.1 或 ≥ 26.12.1，下载地址：{NODE_DOWNLOAD}",
    "docker": f"请安装 Docker Desktop 并启动，下载地址：{DOCKER_DOWNLOAD}",
    "ntqq": "请安装/更新 Windows QQ（NTQQ），本控制台不代管 QQ 版本",
}


def _pip_host_python() -> Optional[str]:
    """找一个能跑 pip 的本机解释器（打包运行时 sys.executable 不能用来装东西）。"""
    from core.paths import is_frozen
    from core.pythonenv import collect

    if not is_frozen():
        return sys.executable
    found = collect()
    return found[0][0] if found else None


@router.get("/detect")
async def detect(force: bool = Query(False, description="跳过 20 秒检测缓存")) -> Dict[str, Any]:
    return await detect_environment(force=force)


@router.post("/install")
async def install_dependencies(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    items: List[str] = []
    raw = payload.get("items")
    if isinstance(raw, list):
        items = [str(x).strip().lower() for x in raw if str(x).strip()]
    elif isinstance(raw, str) and raw.strip():
        items = [raw.strip().lower()]
    if not items:
        raise ConsoleError("请至少指定一项待安装依赖", code="invalid_body", http_status=400)

    adapter = get_platform()
    results: List[Dict[str, Any]] = []
    for item in items:
        if item == "uv":
            # 用本机真实存在的解释器装 uv；打包运行时 sys.executable 是控制台
            # 自己，拿它执行 -m pip 只会把控制台再拉起一次。
            python_exe = _pip_host_python()
            if not python_exe:
                results.append(
                    {
                        "item": item,
                        "ok": False,
                        "exit_code": None,
                        "output": "",
                        "hint": "没有找到可用的 Python 解释器，请先安装 Python 3.12+ 并勾选 Add to PATH",
                    }
                )
                continue
            code, out = await asyncio.to_thread(
                adapter.run_capture,
                [python_exe, "-m", "pip", "install", "-U", "uv"],
                None,
                None,
                600.0,
            )
            ok = code == 0
            results.append(
                {
                    "item": item,
                    "ok": ok,
                    "exit_code": code,
                    "output": out[-1500:],
                    "hint": None if ok else "pip 安装 uv 失败，可尝试手动 pip install uv",
                }
            )
            continue
        hint = MANUAL_HINTS.get(item)
        if not hint:
            raise ConsoleError(
                f"不支持自动安装：{item}",
                code="unsupported_item",
                http_status=400,
                hint="支持自动安装的项：uv；其余需人工安装",
            )
        results.append({"item": item, "ok": False, "manual": True, "hint": hint})

    layout = get_layout()
    settings = get_settings()
    fresh = await detect_environment(force=True, layout=layout, settings=settings)
    return {"results": results, "detect": fresh}
