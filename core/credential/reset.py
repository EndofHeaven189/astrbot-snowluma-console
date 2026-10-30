"""AstrBot 控制台密码重置（需求 6.7）。

* Docker 路线：docker exec -i astrbot astrbot password → docker restart astrbot
* uv 路线：<venv> 内 astrbot（经 PlatformAdapter.venv_executable 解析）→ 重启受控进程

注意：AstrBot 4.x 的 password 命令不会自己生成密码，而是交互式连问两次
「New dashboard password」（hide_input + 二次确认），且要求 ≥8 位、含大小写和数字。
所以这里由控制台生成强随机密码，通过 stdin 喂给 CLI，再把结果写进凭据库；
事后用 cmd_config.json 里的 dashboard 段做校验。
SnowLuma 不提供控制台侧重置。
"""

from __future__ import annotations

import json
import re
import secrets
import string
from typing import Any, Dict, List, Optional

from core.constants import DEPLOY_DOCKER, LOOPBACK, PASSWORD_CAPTURED, SERVICE_ASTRBOT
from core.credential.store import set_credential
from core.errors import CredentialError, PreconditionError
from core.paths import Layout, get_layout
from core.platform import get_platform
from core.service.factory import (
    get_controller,
    storage_project_dir,
    venv_astrbot_exe,
    venv_python_exe,
)
from core.settings import Settings, get_settings
from core.utils import now_iso

PASSWORD_LINE = re.compile(r"(?:密码|password)[^A-Za-z0-9]{0,4}([A-Za-z0-9!@#$%^&*._~+/=-]{6,64})", re.I)

DASHBOARD_USERNAME = "astrbot"
# AstrBot 的复杂度要求：≥8 位、含大写、小写、数字（astrbot.core.utils.auth_password）。
_PWD_ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 16) -> str:
    """生成满足 AstrBot 复杂度策略的随机密码。"""
    body = [secrets.choice(_PWD_ALPHABET) for _ in range(max(length, 10) - 4)]
    core = ["A", "b", "7"] + body          # 保底大写/小写/数字
    secrets.SystemRandom().shuffle(core)
    return "".join(core)


def _dashboard_state(layout: Layout) -> Dict[str, Any]:
    """读 cmd_config.json 的 dashboard 段（该文件带 UTF-8 BOM，必须用 utf-8-sig）。"""
    path = layout.astrbot_config_file
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    dashboard = data.get("dashboard")
    return dashboard if isinstance(dashboard, dict) else {}


def _extract(output: str) -> Optional[str]:
    for line in reversed(output.splitlines()):
        match = PASSWORD_LINE.search(line)
        if match:
            candidate = match.group(1).strip(" .,;:)]}")
            if candidate and len(candidate) >= 6:
                return candidate
    return None


async def reset_astrbot_password(
    *,
    settings: Optional[Settings] = None,
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    settings = settings or get_settings()
    layout = layout or get_layout()
    mode = settings.astrbot_deploy_mode()
    if not mode:
        raise PreconditionError("尚未选择 AstrBot 部署路线，无法重置密码")

    adapter = get_platform()
    password = generate_password()
    # 注意：不要用 astrbot password —— 它没有非交互参数，
    # hide_input 走 getpass，在无 TTY 的子进程里会直接卡死。
    # astrbot conf set dashboard.password 才是非交互入口，且内部调用同一套哈希实现。
    if mode == DEPLOY_DOCKER:
        code, out = adapter.run_capture(
            ["docker", "exec", "astrbot", "astrbot", "conf", "set", "dashboard.password", password],
            timeout=120,
        )
        if code != 0:
            raise CredentialError(
                "Docker 容器内重置密码失败",
                hint="请确认容器 astrbot 正在运行，且镜像内自带 astrbot CLI",
                detail={"exit_code": code, "output": out[-800:]},
            )
        code2, out2 = adapter.run_capture(["docker", "restart", "astrbot"], timeout=180)
        if code2 != 0:
            raise CredentialError(
                "密码已重置，但 docker restart 失败",
                hint="请手动执行 docker restart astrbot",
                detail={"output": out2[-800:]},
            )
    else:
        # FIX-01：CLI 的工作目录必须与 astrbot init/run 一致（项目目录）
        project_dir = storage_project_dir(layout, settings)
        exe = venv_astrbot_exe(layout)
        if exe:
            base = [exe]
        else:
            python = venv_python_exe(layout)
            if not python:
                raise PreconditionError(
                    "未找到 AstrBot 虚拟环境", hint="请先在「安装」页完成 uv/pip 路线安装"
                )
            base = [python, "-m", "astrbot"]
        # 用户名一起写，避免面板里还留着旧的 dashboard.username
        code, out = adapter.run_capture(
            base + ["conf", "set", "dashboard.username", DASHBOARD_USERNAME],
            cwd=project_dir,
            timeout=120,
        )
        code, out = adapter.run_capture(
            base + ["conf", "set", "dashboard.password", password],
            cwd=project_dir,
            timeout=120,
        )
        if code != 0:
            raise CredentialError(
                "重置 AstrBot 密码失败",
                hint="请检查 venv 内 astrbot 是否可用",
                detail={"exit_code": code, "output": out[-800:]},
            )
        controller = get_controller(SERVICE_ASTRBOT, settings=settings, layout=layout)
        if controller.status().running:
            try:
                await controller.restart()
            except Exception as exc:  # 重启失败不影响密码结果
                raise CredentialError(
                    "密码已重置，但重启失败",
                    hint="请到「服务」页手动重启 AstrBot",
                    detail={"error": str(exc), "output": out[-400:]},
                )

    # 校验：CLI 只回显 "Dashboard password updated."，真正的证据在 cmd_config.json
    dashboard = _dashboard_state(layout) if mode != DEPLOY_DOCKER else {}
    verified = None
    if dashboard:
        verified = bool(dashboard.get("password_hash") or dashboard.get("password"))
        if dashboard.get("username") and dashboard.get("username") != DASHBOARD_USERNAME:
            verified = False

    entry = {
        "dashboard_password": password,
        "username": DASHBOARD_USERNAME,
        "dashboard_url": f"http://{LOOPBACK}:{_dashboard_port(settings)}",
        "reset_at": now_iso(),
        "password_status": PASSWORD_CAPTURED,
        "password_source": "console-reset",
    }
    set_credential(SERVICE_ASTRBOT, entry, layout=layout)
    return {
        "service": SERVICE_ASTRBOT,
        "deploy_mode": mode,
        "username": DASHBOARD_USERNAME,
        "password": password,
        "updated": True,
        "verified": verified,
        "raw_output": out[-1200:],
        "hint": None if verified is not False else "CLI 执行成功，但 cmd_config.json 里没读到新的密码哈希，请检查 AstrBot 版本",
    }


def _dashboard_port(settings: Settings) -> int:
    """面板端口：优先用设置里保存的 dashboard_port，回退默认值。"""
    value = settings.get("astrbot.dashboard_port") if hasattr(settings, "get") else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return 6185
