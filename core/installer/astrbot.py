"""AstrBot 安装（需求 5.4 的两条路线）。

uv/pip 路线：在 instances/AstrBot/venv 建虚拟环境并安装 astrbot 包。
Docker 路线：生成 compose 文件并 docker compose up -d。
两条路线共用 instances/AstrBot/data，切路线不丢配置。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from core.constants import (
    ASTRBOT_DOCKER_IMAGE,
    ASTRBOT_REPO,
    DEPLOY_DOCKER,
    DEPLOY_UV,
    DOCKER_HOST_ALIAS,
    MIRROR_PIP_INDEX,
    MIRROR_PIP_HOST,
    MIN_PYTHON,
)
from core.detector.env import latest_release
from core.errors import InstallError
from core.installer.jobs import Job
from core.paths import Layout, get_layout
from core.platform import get_platform
from core.pythonenv import collect, probe_version
from core.service.factory import venv_python_exe
from core.settings import Settings, get_settings
from core.utils import atomic_write_text, now_iso, parse_version

# AstrBot 4.29.3 在 PyPI 上声明 requires_python ">=3.12"。低于这个版本 pip 不会立刻报错，
# 而是回填老版本依赖直到十几分钟后才失败，所以安装前先按 3.12 拦一道。
MIN_PY = MIN_PYTHON
LAUNCHER_NAMES = {"py", "py.exe", "pyw", "pyw.exe"}


def _python_version(exe: str) -> Optional[Tuple[int, int, int]]:
    """探测解释器版本；探测失败返回 None。"""
    return probe_version(exe)


def _find_python() -> Tuple[str, Tuple[int, int, int]]:
    # 用户常常装了 Python 却没勾 Add to PATH，所以扫 PATH + 官方默认安装目录。
    found_versions: list = collect()
    if not found_versions:
        raise InstallError(
            "没有找到可用的 Python",
            hint=f"安装 Python {MIN_PY[0]}.{MIN_PY[1]} 并加入 PATH 后重试。",
        )
    usable = [item for item in found_versions if item[1][:2] >= MIN_PY]
    if not usable:
        newest = max(found_versions, key=lambda item: item[1])
        raise InstallError(
            f"Python 版本过低：{'.'.join(str(x) for x in newest[1])}",
            hint=(
                f"AstrBot 需要 Python {MIN_PY[0]}.{MIN_PY[1]} 及以上，请安装后重试。"
                "（本机已找到的解释器版本见 detail.found）"
            ),
            detail={
                "required": f">={MIN_PY[0]}.{MIN_PY[1]}",
                "found": [
                    {"path": exe, "version": ".".join(str(x) for x in ver)}
                    for exe, ver in found_versions
                ],
            },
        )
    return max(usable, key=lambda item: item[1])


def _pip_env(mirror: bool) -> Dict[str, str]:
    env = dict(os.environ)
    if mirror:
        env["PIP_INDEX_URL"] = MIRROR_PIP_INDEX
        env["PIP_TRUSTED_HOST"] = MIRROR_PIP_HOST
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _compose_text(layout: Layout, settings: Settings) -> str:
    """生成 compose。

    FIX-02：默认 bridge 网络 + extra_hosts 把 host.docker.internal 指到宿主机网关，
    这样容器里的 OneBot 反向 WS 能连回宿主机的 6199；
    高级设置里切到 host 网络时，反过来只保留 network_mode: host（不能再映射端口）。
    """
    dashboard_port = int(settings.get("astrbot.dashboard_port", 6185))
    onebot_port = int(settings.get("bridge.onebot_port", 6199))
    data_dir = layout.astrbot_data.replace("\\", "/")
    network_mode = str(settings.get("astrbot.docker_network_mode", "bridge") or "bridge")
    lines = [
        "services:",
        "  astrbot:",
        f"    image: {ASTRBOT_DOCKER_IMAGE}",
        "    container_name: astrbot",
        "    restart: unless-stopped",
    ]
    if network_mode == "host":
        lines.append("    network_mode: host")
    else:
        lines.append("    ports:")
        lines.append(f'      - "{dashboard_port}:6185"')
        lines.append(f'      - "{onebot_port}:6199"')
        lines.append("    extra_hosts:")
        lines.append(f'      - "{DOCKER_HOST_ALIAS}:host-gateway"')
    lines.append("    volumes:")
    lines.append(f'      - "{data_dir}:/AstrBot/data"')
    return "\n".join(lines) + "\n"


def write_compose(layout: Layout, settings: Settings) -> str:
    """把当前设置渲染成 compose 文件（FIX-02 切换网络退路后重新生成）。"""
    compose_path = write_compose(layout, settings)
    return compose_path

async def install_astrbot(
    job: Job,
    *,
    route: str,
    layout: Optional[Layout] = None,
    settings: Optional[Settings] = None,
    upgrade: bool = False,
) -> Dict[str, Any]:
    layout = layout or get_layout()
    settings = settings or get_settings()
    layout.ensure()
    if route == DEPLOY_UV:
        return await _install_uv(job, layout, settings, upgrade=upgrade)
    if route == DEPLOY_DOCKER:
        return await _install_docker(job, layout, settings, upgrade=upgrade)
    raise InstallError(f"未知的安装路线：{route}", detail={"route": route})


async def _install_uv(job: Job, layout: Layout, settings: Settings, *, upgrade: bool) -> Dict[str, Any]:
    """uv/pip 路线（FIX-01）。

    v0.1.0 用 uv tool install，装到的是用户级 tool 目录，控制台既不能保证落点在
    instances/AstrBot，也无法可靠拿到可执行文件。v0.1.1 改成显式 venv + 显式解释器：
        uv venv <venv> --python <3.12+>
        uv pip install --python <venv>/Scripts/python.exe astrbot
    没有 uv 时退回 pip 等价流程（同样只装进这个 venv）。
    """
    os.makedirs(layout.astrbot_instance, exist_ok=True)
    os.makedirs(layout.astrbot_data, exist_ok=True)
    mirror = bool(settings.mirror_enabled())
    adapter = get_platform()

    exe, ver = await asyncio.to_thread(_find_python)
    job.step("选择 Python", "ok", "%s (%s)" % (exe, ".".join(str(x) for x in ver)))

    python = venv_python_exe(layout)
    recreate = False
    if python and not upgrade:
        existing = await asyncio.to_thread(_python_version, python)
        if existing and existing[:2] < MIN_PY:
            # 老 venv 是用低于 AstrBot 要求的 Python 建的，装不进去，整目录重建。
            job.log(
                "  现有 venv 的 Python %s 低于要求的 %s，重建虚拟环境"
                % (".".join(str(x) for x in existing), ".".join(str(x) for x in MIN_PY))
            )
            recreate = True
            python = None

    uv_exe = adapter.which("uv")
    if python and not upgrade:
        job.step("创建虚拟环境", "skip", "已存在 %s" % layout.astrbot_venv)
    else:
        if recreate:
            await asyncio.to_thread(shutil.rmtree, layout.astrbot_venv, True)
        job.step("创建虚拟环境", "run", layout.astrbot_venv)
        if uv_exe:
            code, out = await asyncio.to_thread(
                adapter.run_capture,
                [uv_exe, "venv", layout.astrbot_venv, "--python", exe],
                None,
                None,
                settings.timeout("dependency_install"),
            )
        else:
            job.log("  未找到 uv，改用 python -m venv（等价流程）")
            code, out = await asyncio.to_thread(
                adapter.run_capture,
                [exe, "-m", "venv", layout.astrbot_venv],
                None,
                None,
                settings.timeout("dependency_install"),
            )
        if code != 0:
            raise InstallError("创建虚拟环境失败", detail={"output": out[-2000:], "uv": bool(uv_exe)})
        python = venv_python_exe(layout)
        if not python:
            raise InstallError("虚拟环境中没有 python 解释器", detail={"venv": layout.astrbot_venv})
        job.step("创建虚拟环境", "ok", python)

    env = _pip_env(mirror)
    timeout = settings.timeout("dependency_install") * 6

    if uv_exe:
        job.step("安装 astrbot", "run", "uv pip install%s astrbot" % (" -U" if upgrade else ""))
        args = [uv_exe, "pip", "install", "--python", python]
        if upgrade:
            args.append("-U")
        args.append("astrbot")
    else:
        job.step("升级 pip", "run", "pip install -U pip" if not mirror else "清华源")
        code, out = await asyncio.to_thread(
            adapter.run_capture,
            [python, "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"],
            None,
            env,
            900.0,
        )
        for line in out.splitlines()[-8:]:
            job.log("  " + line)
        if code != 0:
            raise InstallError("升级 pip 失败", detail={"output": out[-2000:]})
        job.step("升级 pip", "ok")
        job.step("安装 astrbot", "run", "pip install %sastrbot" % ("-U " if upgrade else ""))
        args = [python, "-m", "pip", "install"]
        if upgrade:
            args.append("-U")
        args.append("astrbot")

    # 依赖有 60+ 个，安装往往要十几分钟。逐行转发，否则用户只看到一个不动的进度。
    captured: List[str] = []

    def _on_line(line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        captured.append(line)
        if len(captured) > 4000:
            del captured[:2000]
        if line.startswith(
            ("Collecting ", "Downloading ", "Successfully", "ERROR", "Installing ", "Prepared ", "Installed ")
        ):
            job.log("  " + line[:180])

    code = await asyncio.to_thread(adapter.run_stream, args, _on_line, None, env, timeout)
    out = "\n".join(captured)
    for line in out.splitlines()[-12:]:
        job.log("  " + line)
    if code != 0:
        raise InstallError(
            "安装 astrbot 失败",
            hint="可在设置中开启国内镜像后重试，或手动在该 venv 里执行 pip install astrbot。",
            detail={"output": out[-3000:], "venv": layout.astrbot_venv},
        )
    job.step("安装 astrbot", "ok")

    # 回读版本：直接问 venv 里的解释器。install 成功不等于能 import（缺 DLL 时 import 会炸）。
    version = await asyncio.to_thread(_import_version, python)
    if not version:
        raise InstallError(
            "astrbot 装好了但无法 import",
            hint="通常是运行时缺少依赖或 Python 位数不匹配，请查看任务日志里的详细输出。",
            detail={"venv": layout.astrbot_venv},
        )
    settings.set_astrbot_version(version)
    job.step("校验安装", "ok", "astrbot " + version)

    # init 与 run 必须用同一个 cwd（项目目录），否则数据会落在别处。
    project_dir = settings.astrbot_project_dir(layout.astrbot_instance)
    os.makedirs(project_dir, exist_ok=True)
    exe_astrbot = venv_astrbot_exe(layout)
    if exe_astrbot and not os.path.exists(os.path.join(project_dir, ".astrbot")):
        job.step("初始化目录", "run", "astrbot init -y (cwd=%s)" % project_dir)
        code, out = await asyncio.to_thread(
            adapter.run_capture, [exe_astrbot, "init", "-y"], project_dir, None, 300.0
        )
        for line in out.splitlines()[-6:]:
            job.log("  " + line)
        if code != 0:
            raise InstallError(
                "初始化 AstrBot 目录失败",
                hint="可手动在项目目录执行 astrbot init -y 后重试",
                detail={"output": out[-1500:], "cwd": project_dir},
            )
        job.step("初始化目录", "ok", project_dir)
    _verify_data_dir(project_dir, layout)

    settings.set_astrbot_deploy_mode(DEPLOY_UV)
    settings.save()
    return {
        "installed": True,
        "route": DEPLOY_UV,
        "venv": layout.astrbot_venv,
        "project_dir": project_dir,
        "data_dir": layout.astrbot_data,
        "version": version,
    }


def _import_version(python: str) -> Optional[str]:
    code, out = get_platform().run_capture(
        [python, "-c", "import astrbot;print(getattr(astrbot,'__version__',''))"], None, None, 60.0
    )
    if code != 0:
        return None
    value = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return value or None


def _verify_data_dir(project_dir: str, layout: Layout) -> None:
    """FIX-01：init 之后数据目录必须正好是 instances/AstrBot/data。"""
    expected = os.path.normcase(os.path.abspath(layout.astrbot_data))
    actual = os.path.normcase(os.path.abspath(os.path.join(project_dir, "data")))
    if expected != actual:
        raise InstallError(
            "AstrBot 数据目录位置不对，已中止",
            hint="安装时的 cwd 必须是项目目录（instances/AstrBot），否则配置与凭据会指向另一份数据。",
            detail={"expected": expected, "actual": actual, "project_dir": project_dir},
        )


async def _install_docker(job: Job, layout: Layout, settings: Settings, *, upgrade: bool) -> Dict[str, Any]:
    if not get_platform().which("docker"):
        raise InstallError("未安装 Docker", hint="请先安装 Docker Desktop 并启动。")
    os.makedirs(layout.astrbot_instance, exist_ok=True)
    os.makedirs(layout.astrbot_data, exist_ok=True)

    compose_path = layout.astrbot_compose_file
    os.makedirs(os.path.dirname(compose_path) or ".", exist_ok=True)
    atomic_write_text(compose_path, _compose_text(layout, settings))
    job.step("生成 compose", "ok", compose_path)

    code, out = await asyncio.to_thread(
        get_platform().run_capture,
        ["docker", "compose", "-f", compose_path, "pull"],
        None,
        None,
        3600.0,
    )
    for line in out.splitlines()[-10:]:
        job.log("  " + line)
    if code != 0:
        raise InstallError(
            "拉取镜像失败",
            hint="检查 Docker 是否运行、网络是否可达镜像仓库；也可在设置里开启国内镜像。",
            detail={"output": out[-3000:]},
        )
    job.step("拉取镜像", "ok", ASTRBOT_DOCKER_IMAGE)

    code, out = await asyncio.to_thread(
        get_platform().run_capture,
        ["docker", "compose", "-f", compose_path, "up", "-d"],
        None,
        None,
        900.0,
    )
    for line in out.splitlines()[-10:]:
        job.log("  " + line)
    if code != 0:
        raise InstallError("启动容器失败", detail={"output": out[-3000:]})
    job.step("启动容器", "ok", "astrbot")

    settings.set_astrbot_deploy_mode(DEPLOY_DOCKER)
    settings.save()
    return {"installed": True, "route": DEPLOY_DOCKER, "compose": compose_path, "container": "astrbot"}
