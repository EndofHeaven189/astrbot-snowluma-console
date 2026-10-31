"""控制器工厂 —— 唯一允许读 deploy_mode 的地方（需求 5.4 / 验收 2b）。"""

from __future__ import annotations

import os
from typing import Dict, Optional

from core.constants import (
    DEPLOY_DOCKER,
    DEPLOY_UV,
    LOOPBACK,
    SERVICE_ASTRBOT,
    SERVICE_SNOWLUMA,
)
from core.errors import ServiceError
from core.paths import Layout, get_layout
from core.platform import get_platform
from core.service.base import ServiceController
from core.service.docker import DockerComposeController
from core.service.null import UnconfiguredController
from core.service.process import ProcessController
from core.settings import Settings, get_settings
from core.utils import parse_version


# ---------------------------------------------------------------- 版本探测
def _first_version(text: str) -> Optional[str]:
    if not text:
        return None
    parsed = parse_version(text)
    if parsed:
        return ".".join(str(x) for x in parsed)
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:40] or None


def venv_astrbot_exe(layout: Layout) -> Optional[str]:
    """venv 里的 astrbot 可执行文件。

    路径规则由 PlatformAdapter.venv_executable 决定（Windows: Scripts\\，Linux: bin/），
    业务代码不拼目录名（FIX-01）。
    """
    return get_platform().venv_executable(layout.astrbot_venv, "astrbot")


def venv_python_exe(layout: Layout) -> Optional[str]:
    """venv 里的 Python 解释器；venv 不存在或探测不到都返回 None（FIX-C）。"""
    exe = get_platform().venv_executable(layout.astrbot_venv, "python")
    if exe:
        return exe
    return get_platform().venv_executable(layout.astrbot_venv, "python3")


def astrbot_uv_version(layout: Layout) -> Optional[str]:
    exe = venv_astrbot_exe(layout)
    if not exe:
        return None
    code, out = get_platform().run_capture([exe, "--version"], timeout=25)
    if code != 0:
        return None
    return _first_version(out)


def snowluma_version(layout: Layout) -> Optional[str]:
    """优先读安装时写入的元数据，其次解压包内的版本文件。"""
    from core.utils import read_json

    meta = read_json(os.path.join(layout.snowluma_app, ".console_meta.json"), {})
    if isinstance(meta, dict) and meta.get("version"):
        return str(meta["version"])
    release = read_json(os.path.join(layout.snowluma_app, "release.json"), {})
    if isinstance(release, dict) and release.get("version"):
        return str(release["version"])
    pkg = read_json(os.path.join(layout.snowluma_app, "package.json"), {})
    if isinstance(pkg, dict) and pkg.get("version"):
        return str(pkg["version"])
    return None


def storage_project_dir(layout: Layout, settings: Settings) -> str:
    """AstrBot uv 路线的工作目录（data/ logs/ plugins/ 都在这里，FIX-01）。"""
    return settings.astrbot_project_dir(layout.astrbot_instance)


# ---------------------------------------------------------------- SnowLuma 运行环境
def snowluma_run_dir(layout: Layout) -> str:
    """SnowLuma 的 config/ 与 data/ 相对 cwd，因此 cwd 指到 instances 下。"""
    os.makedirs(layout.snowluma_instance, exist_ok=True)
    os.makedirs(layout.snowluma_config_dir, exist_ok=True)
    return layout.snowluma_instance


def snowluma_entry(layout: Layout) -> str:
    return os.path.join(layout.snowluma_app, "index.mjs")


def snowluma_env(layout: Layout, settings: Optional[Settings] = None) -> Dict[str, str]:
    """SnowLuma 的启动环境变量。

    FIX-G：这里会顺带生成引导密码，所以只能「真正启动之前」调用，
    不能挂在构造控制器上——get_controller() 被 /api/service/status 每 4 秒调一次，
    以前服务还没跑过就先把自己的随机密码标成 captured，并渲染出 ?token=<随机串>。
    """
    from core.credential.eula import accepted_flags
    from core.credential.snowluma import ensure_admin_password

    settings = settings or get_settings()
    env: Dict[str, str] = {}
    env.update(accepted_flags(layout))
    env["SNOWLUMA_WEBUI_PORT"] = str(int(settings.get("snowluma.webui_port", 5099)))
    env["SNOWLUMA_ONEBOT_HTTP_PORT"] = str(int(settings.get("snowluma.http_port", 3000)))
    env["SNOWLUMA_ONEBOT_WS_PORT"] = str(int(settings.get("snowluma.ws_port", 3001)))
    env["SNOWLUMA_LOG_DIR"] = os.path.join(layout.logs, "snowluma")
    env["SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD"] = ensure_admin_password(layout)
    return env


# ---------------------------------------------------------------- 具体构造
def _build_astrbot(settings: Settings, layout: Layout) -> ServiceController:
    mode = settings.astrbot_deploy_mode()
    port = int(settings.get("astrbot.dashboard_port", 6185))
    # FIX-H：桥接端口只有一个真源 bridge.onebot_port（老配置回落 astrbot.onebot_port）。
    bridge_port = settings.onebot_port()
    url = f"http://{LOOPBACK}:{port}"

    if mode == DEPLOY_DOCKER:
        return DockerComposeController(
            SERVICE_ASTRBOT,
            container="astrbot",
            compose_file=layout.astrbot_compose_file,
            port=port,
            bridge_port=bridge_port,
            ready_url=url,
        )
    if mode == DEPLOY_UV:
        exe = venv_astrbot_exe(layout)
        if exe:
            command = [exe]
        else:
            python = venv_python_exe(layout)
            command = [python, "-m", "astrbot"] if python else ["astrbot"]
        command = command + ["run"]
        project_dir = storage_project_dir(layout, settings)
        return ProcessController(
            SERVICE_ASTRBOT,
            command=command,
            cwd=project_dir,
            installed_check=lambda: venv_astrbot_exe(layout) is not None,
            version_getter=lambda: astrbot_uv_version(layout),
            port=port,
            bridge_port=bridge_port,
            ready_url=url,
            preflight=lambda: _require_astrbot_init(project_dir),
        )
    return UnconfiguredController(
        SERVICE_ASTRBOT, "尚未选择 AstrBot 的部署路线（uv/pip 或 Docker）"
    )


def check_data_dir(project_dir: str, layout: Layout) -> None:
    """FIX-01 校验：init 出来的数据目录必须落在 instances\\AstrBot\\data。

    否则说明 cwd 用错了（例如在 venv 目录或临时目录里 init），
    之后 UI 读写的 config 文件会和控制台安装的不是同一份。
    """
    expected = os.path.normcase(os.path.abspath(layout.astrbot_data))
    actual = os.path.normcase(os.path.abspath(os.path.join(project_dir, "data")))
    if expected != actual:
        raise ServiceError(
            "AstrBot 数据目录不在预期位置，拒绝继续",
            hint="安装时的 cwd 必须是项目目录（instances\\AstrBot），否则配置和凭据会指向另一份数据。",
            detail={"expected": expected, "actual": actual},
        )


def _require_astrbot_init(project_dir: str) -> None:
    """AstrBot 4.x 必须 init 过才能 run（否则 CLI 直接退出）。"""
    if os.path.exists(os.path.join(project_dir, ".astrbot")):
        return
    # 4.x 有的版本只在 data/cmd_config.json 留痕，也算初始化过
    if os.path.exists(os.path.join(project_dir, "data", "cmd_config.json")):
        return
    raise ServiceError(
        "AstrBot 目录尚未初始化，无法启动",
        hint="到「安装」页重新执行一次安装（会自动 init），或在该目录手动运行 astrbot init -y",
        detail={"project_dir": project_dir, "missing": ".astrbot"},
    )


def snowluma_installed(layout: Layout) -> bool:
    if not os.path.isdir(layout.snowluma_app):
        return False
    for name in ("index.mjs", "launcher.bat", "SnowLuma.exe", "snowluma.exe"):
        if os.path.exists(os.path.join(layout.snowluma_app, name)):
            return True
    return False


def _build_snowluma(settings: Settings, layout: Layout) -> ServiceController:
    port = int(settings.get("snowluma.webui_port", 5099))
    node = get_platform().which("node")
    entry = snowluma_entry(layout)
    if node and os.path.exists(entry):
        # 直接用 node 跑入口，cwd 指到 instances，用绝对路径加载 native/client
        command = [node, entry]
        shell = False
    else:
        command = [layout.snowluma_launcher]
        shell = True
    return ProcessController(
        SERVICE_SNOWLUMA,
        command=command,
        cwd=snowluma_run_dir(layout),
        installed_check=lambda: snowluma_installed(layout),
        version_getter=lambda: snowluma_version(layout),
        port=port,
        ready_url=f"http://{LOOPBACK}:{port}",
        # FIX-G：惰性求值——密码只在 start() 构建 env 时才生成。
        env_extra=lambda: snowluma_env(layout, settings),
        shell=shell,
    )


def get_controller(
    service: str,
    settings: Optional[Settings] = None,
    layout: Optional[Layout] = None,
) -> ServiceController:
    settings = settings or get_settings()
    layout = layout or get_layout()
    if service == SERVICE_ASTRBOT:
        return _build_astrbot(settings, layout)
    if service == SERVICE_SNOWLUMA:
        return _build_snowluma(settings, layout)
    raise ServiceError(f"未知服务：{service}", detail={"service": service})
