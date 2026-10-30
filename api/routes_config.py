"""配置读写（需求 6.5 / 6.6）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from core.config.store import get_config_store, list_backups, restore_backup
from core.credential.snowluma import detect_history
from core.installer.snowluma import archive_config
from core.errors import ConsoleError
from core.paths import get_layout
from core.settings import get_settings

from api.deps import guard_service

router = APIRouter(prefix="/api/config", tags=["config"])


def _store():
    return get_config_store(get_layout(), get_settings())


@router.get("/{service}")
def read_config(service: str) -> Dict[str, Any]:
    guard_service(service)
    store = _store()
    files: List[Dict[str, Any]] = []
    for meta in store.list_files(service):
        files.append(meta)
    return {"service": service, "files": files}


@router.get("/snowluma/history")
def snowluma_history() -> Dict[str, Any]:
    """SnowLuma 历史数据目录检测（FIX-05）。"""
    return detect_history(get_layout())


@router.post("/snowluma/archive-config")
def snowluma_archive_config() -> Dict[str, Any]:
    """把 instances\\SnowLuma\\config 整体归档到 backups，得到全新数据目录（FIX-05）。"""
    archived = archive_config(get_layout())
    return {"archived": archived, "history": detect_history(get_layout())}


@router.get("/{service}/{file_id}")
def read_config_file(service: str, file_id: str) -> Dict[str, Any]:
    guard_service(service)
    store = _store()
    return store.read(service, file_id)


class ConfigPayload(BaseModel):
    data: Any
    reason: Optional[str] = "manual"
    backup: bool = True


class PatchPayload(BaseModel):
    patch: Dict[str, Any]
    reason: Optional[str] = "patch"
    backup: bool = True


@router.put("/{service}/{file_id}")
def write_config_file(service: str, file_id: str, payload: ConfigPayload) -> Dict[str, Any]:
    guard_service(service)
    store = _store()
    result = store.write(
        service, file_id, payload.data, backup=payload.backup, reason=payload.reason or "manual"
    )
    return result


@router.patch("/{service}/{file_id}")
def patch_config_file(service: str, file_id: str, payload: PatchPayload) -> Dict[str, Any]:
    guard_service(service)
    store = _store()
    return store.patch(
        service, file_id, payload.patch, backup=payload.backup, reason=payload.reason or "patch"
    )


@router.get("/{service}/backups/list")
def backups(service: str) -> Dict[str, Any]:
    guard_service(service)
    return {"service": service, "backups": list_backups(service, layout=get_layout())}


@router.post("/backups/restore")
def restore(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ConsoleError("请提供备份文件名", code="invalid_body", http_status=400)
    return restore_backup(name, layout=get_layout(), settings=get_settings())
