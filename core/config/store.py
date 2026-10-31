"""配置读写（需求 6.5）。

要点：
- 改前必备份到 backups/<服务>_<文件名>_<时间戳>.json；
- 原子写，避免半截文件；
- 返回 restart_required，由 UI 提示重启；
- SnowLuma 的 HTTP(3000) 与 WS(3001) 是两套独立 server，accessToken 各自独立。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.constants import SERVICE_ASTRBOT, SERVICE_SNOWLUMA
from core.errors import BackupError, ConfigError
from core.paths import Layout, get_layout
from core.settings import Settings, get_settings
from core.utils import (
    atomic_write_json,
    now_iso,
    read_json,
    timestamp_slug,
)


@dataclass(frozen=True)
class ConfigFileSpec:
    id: str
    label: str
    service: str
    rel_hint: str
    dynamic: bool = False


SPECS: Dict[str, List[ConfigFileSpec]] = {
    SERVICE_ASTRBOT: [
        ConfigFileSpec("cmd_config", "cmd_config.json（主配置）", SERVICE_ASTRBOT, "data/cmd_config.json"),
    ],
    SERVICE_SNOWLUMA: [
        ConfigFileSpec("runtime", "runtime.json（运行参数）", SERVICE_SNOWLUMA, "config/runtime.json"),
        ConfigFileSpec("onebot", "onebot.json（协议端）", SERVICE_SNOWLUMA, "config/onebot.json"),
        ConfigFileSpec("onebot_account", "onebot_<QQ>.json（账号）", SERVICE_SNOWLUMA, "config/onebot_*.json", dynamic=True),
    ],
}


def _pick_config_dir(layout: Layout, settings: Settings) -> str:
    """SnowLuma 的 config 目录相对进程 cwd，优先用 instances 下的数据目录。

    FIX-BG：以前这里读 snowluma.config_dir，但 DEFAULT_SETTINGS 里没有这个键，
    分支永远走不到（死代码）。要么补默认值并在设置页暴露入口，要么删掉；
    这里按后者处理，保留注释说明，避免下次又有人以为它能用。
    """
    candidates = [
        layout.snowluma_config_dir,
        os.path.join(layout.snowluma_app, "config"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return layout.snowluma_config_dir


class ConfigStore:
    def __init__(self, layout: Optional[Layout] = None, settings: Optional[Settings] = None) -> None:
        self.layout = layout or get_layout()
        self.settings = settings or get_settings()
        self._snowluma_config_dir: Optional[str] = None

    # ------------------------------------------------------------ 路径解析
    def snowluma_config_dir(self) -> str:
        if self._snowluma_config_dir is None:
            self._snowluma_config_dir = _pick_config_dir(self.layout, self.settings)
        return self._snowluma_config_dir

    def resolve_path(self, service: str, file_id: str) -> str:
        if service == SERVICE_ASTRBOT:
            if file_id != "cmd_config":
                raise ConfigError(f"未知的 AstrBot 配置文件：{file_id}")
            return self.layout.astrbot_config_file
        if service == SERVICE_SNOWLUMA:
            base = self.snowluma_config_dir()
            if file_id == "runtime":
                return os.path.join(base, "runtime.json")
            if file_id == "onebot":
                return os.path.join(base, "onebot.json")
            if file_id.startswith("onebot_"):
                return os.path.join(base, file_id + ".json")
            raise ConfigError(f"未知的 SnowLuma 配置文件：{file_id}")
        raise ConfigError(f"未知服务：{service}")

    def list_files(self, service: str) -> List[Dict[str, Any]]:
        specs = SPECS.get(service)
        if specs is None:
            raise ConfigError(f"未知服务：{service}")
        out: List[Dict[str, Any]] = []
        for spec in specs:
            if spec.dynamic:
                base = self.snowluma_config_dir()
                pattern = os.path.join(base, "onebot_*.json")
                for path in sorted(glob.glob(pattern)):
                    stem = os.path.basename(path)[: -len(".json")]
                    out.append(self._describe(spec, stem, path, spec.label))
                continue
            path = self.resolve_path(service, spec.id)
            out.append(self._describe(spec, spec.id, path, spec.label))
        return out

    def _describe(self, spec: ConfigFileSpec, file_id: str, path: str, label: str) -> Dict[str, Any]:
        exists = os.path.isfile(path)
        stat = None
        if exists:
            try:
                stat = os.stat(path)
            except OSError:
                stat = None
        return {
            "id": file_id,
            "label": label,
            "service": spec.service,
            "path": path,
            "exists": exists,
            "size_mb": round(stat.st_size / (1024 * 1024), 3) if stat else 0.0,
            "modified_at": (
                None
                if not stat
                else __import__("datetime").datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            ),
        }

    # ------------------------------------------------------------ 读
    def read(self, service: str, file_id: str) -> Dict[str, Any]:
        path = self.resolve_path(service, file_id)
        exists = os.path.isfile(path)
        data = read_json(path, None) if exists else None
        if data is None and exists:
            raise ConfigError(
                "配置文件不是合法 JSON，未做任何修改",
                hint="请检查文件是否被手工编辑破坏，或从备份还原。",
                detail={"path": path},
            )
        return {
            "service": service,
            "id": file_id,
            "path": path,
            "exists": exists,
            "data": data if data is not None else {},
            # 配置读写也要读同一份 deploy_mode（需求 5.4）：
            # 两条路线共用 instances 下的数据目录，这里只做展示与校验，不改路径。
            "deploy_mode": self.settings.astrbot_deploy_mode() if service == SERVICE_ASTRBOT else None,
        }

    def read_raw(self, service: str, file_id: str, default: Any = None) -> Any:
        path = self.resolve_path(service, file_id)
        if not os.path.isfile(path):
            return default
        data = read_json(path, default)
        return data if data is not None else default

    # ------------------------------------------------------------ 备份
    def backup(self, service: str, file_id: str, *, reason: str = "before-write") -> Optional[str]:
        path = self.resolve_path(service, file_id)
        if not os.path.isfile(path):
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        # 同一秒内连续保存要能生成多个备份，不能互相覆盖（需求 6.5：改 3 次 → 3 个备份）。
        slug = timestamp_slug()
        name = f"{service}_{stem}_{slug}.json"
        target = os.path.join(self.layout.backups, name)
        serial = 1
        while os.path.exists(target):
            serial += 1
            name = f"{service}_{stem}_{slug}-{serial}.json"
            target = os.path.join(self.layout.backups, name)
        try:
            os.makedirs(self.layout.backups, exist_ok=True)
            data = read_json(path, None)
            if data is None:
                raise BackupError("原文件无法解析，已中止写入以免丢数据", detail={"path": path})
            atomic_write_json(target, {"_meta": {"service": service, "file": path, "reason": reason, "created_at": now_iso()}, "data": data})
        except OSError as exc:
            raise BackupError("备份写入失败", detail={"path": target, "error": str(exc)}) from exc
        return target

    # ------------------------------------------------------------ 写
    def write(
        self,
        service: str,
        file_id: str,
        data: Any,
        *,
        backup: bool = True,
        reason: str = "manual",
    ) -> Dict[str, Any]:
        if not isinstance(data, (dict, list)):
            raise ConfigError("配置内容必须是对象或数组")
        path = self.resolve_path(service, file_id)
        backup_path = self.backup(service, file_id, reason=reason) if backup else None
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # AstrBot 的 cmd_config.json 带 UTF-8 BOM，保持原样写回，别把格式改掉。
        atomic_write_json(path, data, bom=_has_bom(path))
        return {
            "service": service,
            "id": file_id,
            "path": path,
            "backup": backup_path,
            "written_at": now_iso(),
            "restart_required": True,
        }

    def patch(
        self,
        service: str,
        file_id: str,
        patch_data: Dict[str, Any],
        *,
        backup: bool = True,
        reason: str = "patch",
    ) -> Dict[str, Any]:
        from core.utils import merge_dict

        current = self.read_raw(service, file_id, {}) or {}
        if not isinstance(current, dict):
            raise ConfigError("配置文件根节点不是对象，无法做局部修改", detail={"path": self.resolve_path(service, file_id)})
        merged = merge_dict(current, patch_data)
        return self.write(service, file_id, merged, backup=backup, reason=reason)


# ---------------------------------------------------------------- 备份列表与还原
def _has_bom(path: str) -> bool:
    """原文件是否带 UTF-8 BOM（不存在时按无 BOM 处理）。"""
    try:
        with open(path, "rb") as fh:
            return fh.read(3) == b"\xef\xbb\xbf"
    except OSError:
        return False


def _backup_index(layout: Layout) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(layout.backups):
        return index
    for name in os.listdir(layout.backups):
        if not name.endswith(".json"):
            continue
        path = os.path.join(layout.backups, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        index[name] = {
            "name": name,
            "path": path,
            "size_mb": round(stat.st_size / (1024 * 1024), 4),
            "created_at": __import__("datetime").datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        }
    return index


def list_backups(service: Optional[str] = None, layout: Optional[Layout] = None) -> List[Dict[str, Any]]:
    layout = layout or get_layout()
    entries = list(_backup_index(layout).values())
    if service:
        prefix = f"{service}_"
        entries = [e for e in entries if e["name"].startswith(prefix)]
    entries.sort(key=lambda e: e["name"], reverse=True)
    for entry in entries:
        payload = read_json(entry["path"], {}) or {}
        meta = payload.get("_meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            entry["original"] = meta.get("file")
            entry["reason"] = meta.get("reason")
            entry["service"] = meta.get("service")
    return entries


def restore_backup(
    name: str,
    *,
    layout: Optional[Layout] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    layout = layout or get_layout()
    safe = os.path.basename(name)
    path = os.path.join(layout.backups, safe)
    if not os.path.isfile(path):
        raise BackupError("备份不存在", detail={"name": safe})
    payload = read_json(path, None)
    if not isinstance(payload, dict) or "data" not in payload:
        raise BackupError("备份内容损坏，缺少 data 段", detail={"name": safe})
    meta = payload.get("_meta") or {}
    target = meta.get("file")
    if not target or not isinstance(target, str):
        raise BackupError("备份缺少原始路径信息，无法自动还原", detail={"name": safe})
    # FIX-AN：还原目标直接取自备份里的 _meta.file，没有任何约束，可以写到布局之外。
    # 只允许落在当前数据根目录内。
    root_prefix = os.path.normcase(os.path.abspath(layout.root))
    target_abs = os.path.normcase(os.path.abspath(target))
    if target_abs != root_prefix and not target_abs.startswith(root_prefix + os.sep):
        raise BackupError(
            "备份记录的原始路径不在当前数据目录内，已拒绝还原",
            hint="这条备份可能来自另一个数据根目录；请只还原当前 root 下的配置，或先切回那个 root。",
            detail={"name": safe, "target": target, "root": layout.root},
        )
    service = meta.get("service")
    store = ConfigStore(layout, settings)
    file_id: Optional[str] = None
    if service == SERVICE_ASTRBOT:
        file_id = "cmd_config"
    elif service == SERVICE_SNOWLUMA:
        # FIX-AB：两个分支等价，直接取文件名主干。
        file_id = os.path.splitext(os.path.basename(target))[0]
    if file_id is None:
        raise BackupError("无法判断备份对应的配置项", detail={"name": safe, "target": target})
    current_backup = store.backup(service, file_id, reason="before-restore")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    # 还原也要保住原文件的 BOM 形态，否则 AstrBot 下次读配置照样能读（utf-8-sig），
    # 但文件形态被悄悄改掉，属于没必要的副作用。
    atomic_write_json(target, payload["data"], bom=_has_bom(target))
    return {
        "restored": target,
        "service": service,
        "id": file_id,
        "from": path,
        "current_backup": current_backup,
        "restart_required": True,
    }


def get_config_store(
    layout: Optional[Layout] = None, settings: Optional[Settings] = None
) -> ConfigStore:
    return ConfigStore(layout, settings)
