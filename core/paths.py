"""目录布局与「引导配置」。

设计要点（对应需求 5.2 节：程序与数据分离）：
    <root>\\apps\\       程序目录，整体删掉重装不影响数据
    <root>\\instances\\  数据目录，迁移时只拷这一个
    <root>\\downloads\\  下载缓存
    <root>\\backups\\    改配置前的自动备份
    <root>\\logs\\       控制台自身日志 + 抓取的服务日志
    <root>\\state\\      控制台状态（settings / 凭据库 / 检测缓存）

难点：root 本身是一个「设置」，但设置文件要落在 <root>\\state 下。
因此采用两级配置：
    1. 引导配置 bootstrap.json —— 位置固定在用户目录，只存 root_dir
    2. 主配置 <root>\\state\\settings.json —— 其余全部设置
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.constants import (
    APP_SHORT,
    DIR_APPS,
    DIR_BACKUPS,
    DIR_DOWNLOADS,
    DIR_INSTANCES,
    DIR_LOGS,
    DIR_STATE,
)
from core.errors import PathError

_BOOTSTRAP_LOCK = threading.RLock()


def bootstrap_dir() -> Path:
    """引导配置所在目录（与 root 无关，保证 root 可改）。"""
    override = os.environ.get("ASTRBOT_CONSOLE_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_SHORT
        return Path.home() / ("." + APP_SHORT.lower())
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_SHORT
    return Path.home() / (".config/" + APP_SHORT.lower())


def bootstrap_file() -> Path:
    return bootstrap_dir() / "bootstrap.json"


def read_bootstrap() -> Dict[str, Any]:
    path = bootstrap_file()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_bootstrap(data: Dict[str, Any]) -> None:
    """原子写入引导配置。"""
    with _BOOTSTRAP_LOCK:
        path = bootstrap_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """程序自身所在目录：打包后是 exe 所在目录，源码运行时是项目根目录。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return str(Path(__file__).resolve().parent.parent)


def bundled_dir() -> str:
    """随程序分发的只读资源目录：打包后是 PyInstaller 的解包目录。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return app_dir()


def web_dist_dir() -> str:
    """前端构建产物位置（单端口托管用）。"""
    return os.path.join(bundled_dir(), "web", "dist")


def default_root_dir() -> str:
    """默认根目录。

    源码运行：项目目录（main.py 的上一级），apps / instances / downloads /
    backups / logs / state 就落在项目旁边。
    打包运行：exe 同级的 data 目录——程序文件与运行数据分开，卸载装程序时
    不会顺手删掉 instances；安装时选的目录决定这一切落在哪里。
    想换地方可以用 ASTRBOT_CONSOLE_ROOT 环境变量或引导配置里的 root_dir。
    """
    if is_frozen():
        return os.path.join(app_dir(), "data")
    return app_dir()


def resolve_root_dir() -> str:
    """当前生效的根目录：引导配置 > 环境变量 > 平台默认值。"""
    env_root = os.environ.get("ASTRBOT_CONSOLE_ROOT")
    if env_root:
        return os.path.abspath(env_root)
    root = read_bootstrap().get("root_dir")
    if isinstance(root, str) and root.strip():
        return os.path.abspath(root)
    return default_root_dir()


class Layout:
    """一套根目录下的全部路径。所有模块只通过它拼路径，不写字面量。"""

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = os.path.abspath(root or resolve_root_dir())

    # ---- 顶层目录 ----
    @property
    def apps(self) -> str:
        return os.path.join(self.root, DIR_APPS)

    @property
    def instances(self) -> str:
        return os.path.join(self.root, DIR_INSTANCES)

    @property
    def downloads(self) -> str:
        return os.path.join(self.root, DIR_DOWNLOADS)

    @property
    def backups(self) -> str:
        return os.path.join(self.root, DIR_BACKUPS)

    @property
    def logs(self) -> str:
        return os.path.join(self.root, DIR_LOGS)

    @property
    def state(self) -> str:
        return os.path.join(self.root, DIR_STATE)

    # ---- AstrBot ----
    @property
    def astrbot_app(self) -> str:
        return os.path.join(self.apps, "AstrBot")

    @property
    def astrbot_instance(self) -> str:
        return os.path.join(self.instances, "AstrBot")

    @property
    def astrbot_data(self) -> str:
        return os.path.join(self.astrbot_instance, "data")

    @property
    def astrbot_venv(self) -> str:
        return os.path.join(self.astrbot_instance, "venv")

    @property
    def astrbot_plugins(self) -> str:
        return os.path.join(self.astrbot_instance, "plugins")

    @property
    def astrbot_config_file(self) -> str:
        return os.path.join(self.astrbot_data, "cmd_config.json")

    @property
    def astrbot_compose_file(self) -> str:
        return os.path.join(self.astrbot_app, "compose.yml")

    # ---- SnowLuma ----
    @property
    def snowluma_app(self) -> str:
        return os.path.join(self.apps, "SnowLuma")

    @property
    def snowluma_instance(self) -> str:
        return os.path.join(self.instances, "SnowLuma")

    @property
    def snowluma_config_dir(self) -> str:
        return os.path.join(self.snowluma_instance, "config")

    @property
    def snowluma_launcher(self) -> str:
        return os.path.join(self.snowluma_app, "launcher.bat")

    # ---- 状态文件 ----
    @property
    def settings_file(self) -> str:
        return os.path.join(self.state, "settings.json")

    @property
    def credentials_file(self) -> str:
        return os.path.join(self.state, "credentials.json")

    @property
    def eula_file(self) -> str:
        return os.path.join(self.state, "eula_acceptance.json")

    @property
    def detect_cache_file(self) -> str:
        return os.path.join(self.state, "detect_cache.json")

    def log_file(self, service: str) -> str:
        return os.path.join(self.logs, service + ".log")

    def app_log_file(self) -> str:
        return os.path.join(self.logs, "console.log")

    def backup_dir(self, service: str) -> str:
        return os.path.join(self.backups, service)

    # ---- 操作 ----
    def ensure(self) -> "Layout":
        """创建全部标准目录；失败时抛出可读的 PathError。"""
        for path in (
            self.root, self.apps, self.instances, self.downloads,
            self.backups, self.logs, self.state,
            self.astrbot_app, self.astrbot_instance,
            self.snowluma_app, self.snowluma_instance, self.snowluma_config_dir,
        ):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as exc:
                raise PathError(
                    f"无法创建目录 {path}：{exc}",
                    hint="请换一个可写目录（设置 → 目录），或以管理员身份重新启动控制台。",
                    detail={"path": path},
                ) from exc
        return self

    def as_dict(self) -> Dict[str, str]:
        return {
            "root": self.root,
            "apps": self.apps,
            "instances": self.instances,
            "downloads": self.downloads,
            "backups": self.backups,
            "logs": self.logs,
            "state": self.state,
        }


_layout_lock = threading.RLock()
_layout: Optional[Layout] = None


def get_layout() -> Layout:
    """进程级单例；根目录变更后调用 reset_layout() 再取。"""
    global _layout
    with _layout_lock:
        if _layout is None:
            _layout = Layout()
        return _layout


def reset_layout(root: Optional[str] = None) -> Layout:
    global _layout
    with _layout_lock:
        _layout = Layout(root)
        return _layout
