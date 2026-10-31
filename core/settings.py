"""控制台设置。

唯一事实来源：astrbot.deploy_mode 只在这里定义与读取，
服务管理 / 配置读写 / 凭据重置三处必须调用 settings.astrbot_deploy_mode()，
不允许各自判断（对应需求 5.4 节与验收标准 2b）。
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, Optional

from core.constants import (
    DEFAULT_ASTRBOT_DASHBOARD_PORT,
    TIMEOUT_DEFAULTS,
    DEFAULT_ASTRBOT_ONEBOT_PORT,
    DEFAULT_CONSOLE_PORT,
    DEFAULT_SNOWLUMA_HTTP_PORT,
    DEFAULT_SNOWLUMA_WEBUI_PORT,
    DEFAULT_SNOWLUMA_WS_PORT,
    DEPLOY_DOCKER,
    DEPLOY_MODES,
    DEPLOY_UV,
    LOOPBACK,
    PORT_ONEBOT,
)
from core.errors import SettingsError
from core.paths import Layout, get_layout, reset_layout, write_bootstrap

_LOCK = threading.RLock()

DEFAULT_SETTINGS: Dict[str, Any] = {
    "version": 1,
    "console": {
        "host": LOOPBACK,
        "port": DEFAULT_CONSOLE_PORT,
    },
    "mirror": {
        "enabled": True,
        "pypi": "",
        "github": "",
        "docker": "",
    },
    "astrbot": {
        # None 表示「尚未选择部署路线」；检测项据此显示为「待选择」
        "deploy_mode": None,
        "dashboard_port": DEFAULT_ASTRBOT_DASHBOARD_PORT,
        "onebot_port": DEFAULT_ASTRBOT_ONEBOT_PORT,
        # FIX-01：只保留 venv 策略（uv tool install 落点不可控，v0.1.1 起废弃）
        "uv_strategy": "venv",
        # 安装后回读的 astrbot 包版本（FIX-01），None 表示还没装
        "version": None,
        # 用户自定义的项目目录（空 = instances\AstrBot）；init 与 run 共用同一份
        "project_dir": "",
        # FIX-02 退路：bridge（默认，端口映射）或 host（network_mode: host）
        "docker_network_mode": "bridge",
    },
    "snowluma": {
        "webui_port": DEFAULT_SNOWLUMA_WEBUI_PORT,
        "http_port": DEFAULT_SNOWLUMA_HTTP_PORT,
        "ws_port": DEFAULT_SNOWLUMA_WS_PORT,
        # 与安装默认值、前端默认值保持一致；lite 包更小，但要本机 Node ≥ 24.22.1
        "variant": "lite",
    },
    "bridge": {
        "onebot_port": DEFAULT_ASTRBOT_ONEBOT_PORT,
        "token": "",
    },
    # FIX-03：分级超时，全部可在设置页改
    "timeouts": dict(TIMEOUT_DEFAULTS),
    # FIX-07：控制台退出时是否连带停掉自己拉起的子进程
    "lifecycle": {
        "shutdown_child_processes": True,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    """state\\settings.json 的对象封装。"""

    def __init__(self, layout: Optional[Layout] = None) -> None:
        self._layout = layout or get_layout()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_SETTINGS)
        self._loaded = False

    # ---- 生命周期 ----
    def load(self, force: bool = False) -> "Settings":
        with _LOCK:
            if self._loaded and not force:
                return self
            path = self._layout.settings_file
            if os.path.exists(path):
                try:
                    # utf-8-sig：外部工具（PowerShell 5.1 的 -Encoding utf8 等）
                    # 写出来的 settings.json 可能带 BOM，用 utf-8 读会直接崩在启动阶段。
                    with open(path, "r", encoding="utf-8-sig") as fh:
                        raw = json.load(fh)
                    if not isinstance(raw, dict):
                        raise SettingsError(
                            "settings.json 顶层结构不是对象",
                            hint="删除该文件可恢复默认设置。",
                            detail={"path": path},
                        )
                    self._data = _deep_merge(DEFAULT_SETTINGS, raw)
                except json.JSONDecodeError as exc:
                    raise SettingsError(
                        f"settings.json 解析失败：{exc}",
                        hint="删除该文件可恢复默认设置。",
                        detail={"path": path},
                    ) from exc
            else:
                self._data = copy.deepcopy(DEFAULT_SETTINGS)
                self._save_locked()
            self._loaded = True
            return self

    def _save_locked(self) -> None:
        os.makedirs(self._layout.state, exist_ok=True)
        path = self._layout.settings_file
        tmp = path + ".tmp"
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        self._append_history(text)

    def _append_history(self, text: str) -> None:
        """state\\settings.history.json：留最近 20 版设置快照，方便回滚（v0.1.1）。"""
        path = os.path.join(self._layout.state, "settings.history.json")
        try:
            from core.utils import atomic_write_json, now_iso, read_json

            data = read_json(path, {})
            entries = data.get("entries") if isinstance(data, dict) else None
            entries = list(entries) if isinstance(entries, list) else []
            if entries and entries[-1].get("body") == text:
                return
            entries.append({"saved_at": now_iso(), "body": text})
            atomic_write_json(path, {"entries": entries[-20:]})
        except OSError:
            pass

    def save(self) -> None:
        with _LOCK:
            self._save_locked()
            self._loaded = True

    # ---- 读取 ----
    def snapshot(self) -> Dict[str, Any]:
        with _LOCK:
            return copy.deepcopy(self._data)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---- 写入 ----
    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """深合并写入；不会因为部分字段缺失而丢数据。

        FIX-AF / FIX-BA：这里以前对含点号的键走 _set_path 直接写，等于绕过了
        PUT /api/settings 的全部校验（deploy_mode 保护、console.host 白名单、
        端口范围、超时边界都能被 `{"bridge.token": ...}` 这种写法改掉）。
        现在只接受嵌套结构；带点号的键一律拒绝，调用方负责先展开。
        """
        with _LOCK:
            for key, value in (patch or {}).items():
                if "." in str(key):
                    raise SettingsError(
                        f"设置键不能包含点号：{key}",
                        hint='请传嵌套结构，例如 {"bridge": {"token": "..."}}，不要用 bridge.token',
                        detail={"key": key},
                    )
                self._data = _deep_merge(self._data, {key: value})
            self._save_locked()
            return copy.deepcopy(self._data)


    def set_root_dir(self, root: str) -> Layout:
        """切换根目录：写引导配置 + 重置 Layout 单例。"""
        from core.paths import Layout as _Layout

        root = os.path.abspath(root)
        layout = _Layout(root)
        layout.ensure()
        write_bootstrap({"root_dir": root})
        new_layout = reset_layout(root)
        self._layout = new_layout
        self._loaded = False
        self.load(force=True)
        return new_layout

    # ---- 语义化访问器 ----
    @property
    def layout(self) -> Layout:
        return self._layout

    def astrbot_deploy_mode(self) -> Optional[str]:
        mode = self.get("astrbot.deploy_mode")
        return mode if mode in DEPLOY_MODES else None

    def set_astrbot_deploy_mode(self, mode: Optional[str]) -> None:
        if mode is not None and mode not in DEPLOY_MODES:
            raise SettingsError(
                f"未知的部署路线：{mode}",
                hint="只支持 uv 或 docker。",
            )
        self.update({"astrbot": {"deploy_mode": mode}})

    def onebot_port(self) -> int:
        """OneBot 反向 WS 端口：唯一真源是 bridge.onebot_port（FIX-H）。

        astrbot.onebot_port 只为兼容旧 settings.json 而保留，任何读取都先取
        bridge.onebot_port，再回落旧键，最后回落常量。非法值跳过，不抛异常。
        """
        for path in ("bridge.onebot_port", "astrbot.onebot_port"):
            try:
                value = int(self.get(path))
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 65535:
                return value
        return int(PORT_ONEBOT)

    def is_docker(self) -> bool:
        return self.astrbot_deploy_mode() == DEPLOY_DOCKER

    def is_uv(self) -> bool:
        return self.astrbot_deploy_mode() == DEPLOY_UV

    def timeout(self, name: str) -> float:
        """取分级超时（秒）。缺项回落到常量默认值。"""
        value = self.get(f"timeouts.{name}")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return float(TIMEOUT_DEFAULTS.get(name, 60))

    def shutdown_child_processes(self) -> bool:
        return bool(self.get("lifecycle.shutdown_child_processes", True))

    def astrbot_version(self) -> Optional[str]:
        return self.get("astrbot.version") or None

    def set_astrbot_version(self, version: Optional[str]) -> None:
        self.update({"astrbot": {"version": version}})

    def astrbot_project_dir(self, fallback: str) -> str:
        """项目目录（init / run / 重置密码三处共用，FIX-01）。"""
        custom = str(self.get("astrbot.project_dir", "") or "").strip()
        return custom or fallback

    def mirror_enabled(self) -> bool:
        return bool(self.get("mirror.enabled", True))

    def github_proxy(self) -> str:
        """GitHub 下载代理前缀（例如 https://ghfast.top/），空字符串表示直连。

        只影响 GitHub Release 资产下载，不影响 PyPI / Docker。
        """
        if not self.mirror_enabled():
            return ""
        return str(self.get("mirror.github", "") or "").strip().rstrip("/")

    def dynamic_defaults(self) -> Dict[str, Any]:
        """给前端用的默认值集合。"""
        return {
            "deploy_modes": list(DEPLOY_MODES),
            "deploy_uv": DEPLOY_UV,
            "deploy_docker": DEPLOY_DOCKER,
            "console_port": DEFAULT_CONSOLE_PORT,
            "astrbot_dashboard_port": DEFAULT_ASTRBOT_DASHBOARD_PORT,
            "astrbot_onebot_port": DEFAULT_ASTRBOT_ONEBOT_PORT,
            "snowluma_webui_port": DEFAULT_SNOWLUMA_WEBUI_PORT,
        }


_instance: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    global _instance
    with _LOCK:
        if _instance is None:
            _instance = Settings(get_layout())
        if reload:
            _instance._loaded = False
        return _instance.load()
