"""凭据（需求 6.7）。

凭据库是明文文件，只靠文件权限保护（仅当前用户可读），
所以这里不做额外加密，但也不会把密码写进日志。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from core.constants import SERVICE_SNOWLUMA
from core.credential import capture as credential_capture
from core.credential import store as credential_store
from core.credential.reset import reset_astrbot_password
from core.credential.snowluma import detect_history, login_url, sync_login_url, webui_url
from core.errors import CredentialError, PreconditionError
from core.paths import get_layout

from api.deps import guard_service

router = APIRouter(prefix="/api/credential", tags=["credential"])


@router.get("/{service}")
def read_credential(service: str, reveal: bool = Query(True)) -> Dict[str, Any]:
    guard_service(service)
    layout = get_layout()
    entry = credential_store.get_credential(service, layout)
    summary_items = credential_store.summarize(layout)
    summary = next(
        (item for item in summary_items if item.get("service") == service), {}
    ) if isinstance(summary_items, list) else {}
    payload: Dict[str, Any] = {
        "service": service,
        "file": layout.credentials_file,
        "fields": list(entry.keys()),
        "values": entry,
        "summary": summary,
    }
    payload["capture"] = credential_capture.status(service)
    if service == SERVICE_SNOWLUMA:
        payload["login_url"] = login_url(layout=layout)
        payload["webui_url"] = webui_url(layout)
        payload["history"] = detect_history(layout)
    return payload


@router.put("/{service}")
def update_credential(service: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    guard_service(service)
    data = payload.get("values")
    if not isinstance(data, dict):
        raise PreconditionError("values 必须是对象", code="invalid_body")
    layout = get_layout()
    credential_store.set_credential(service, data, layout=layout)
    result: Dict[str, Any] = {"service": service, "updated": list(data.keys())}
    if service == SERVICE_SNOWLUMA:
        result["login_url"] = sync_login_url(layout)
    return result


@router.post("/{service}/recapture")
async def recapture_credential(service: str) -> Dict[str, Any]:
    """FIX-04 补救入口一：重新扫日志（不重启服务也能捞到）。"""
    guard_service(service)
    return credential_capture.recapture(service)


@router.post("/{service}/manual")
def manual_credential(service: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """FIX-04 补救入口二：手动粘贴密码入库。"""
    guard_service(service)
    body = payload or {}
    password = body.get("password") or body.get("value")
    if not isinstance(password, str) or not password.strip():
        raise CredentialError("password 不能为空", code="invalid_body", http_status=400)
    return credential_capture.manual(service, password, str(body.get("note") or ""))


@router.post("/{service}/reset")
async def reset_credential(
    service: str, payload: Dict[str, Any] | None = None, confirm: bool = Query(False)
) -> Dict[str, Any]:
    guard_service(service)
    if service == SERVICE_SNOWLUMA:
        raise PreconditionError(
            "SnowLuma 不支持从控制台重置密码",
            hint="请在 SnowLuma WebUI 的账号设置里修改，控制台只负责保存与展示",
        )
    return await reset_astrbot_password()
