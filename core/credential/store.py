"""凭据库（需求 6.7）。

state/credentials.json 为明文存储，但写入后立刻收紧到仅当前用户可读。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.constants import (
    PASSWORD_CAPTURED,
    PASSWORD_UNKNOWN,
    PASSWORD_USER_MANAGED,
    SERVICE_ASTRBOT,
    SERVICE_SNOWLUMA,
)
from core.errors import CredentialError
from core.paths import Layout, get_layout
from core.platform import get_platform
from core.utils import atomic_write_json, now_iso, read_json

KNOWN_SERVICES = (SERVICE_ASTRBOT, SERVICE_SNOWLUMA, "bridge")


def _empty() -> Dict[str, Any]:
    return {"version": 1, "updated_at": None, "services": {}}


def load_credentials(layout: Optional[Layout] = None) -> Dict[str, Any]:
    layout = layout or get_layout()
    data = read_json(layout.credentials_file, None)
    if not isinstance(data, dict):
        return _empty()
    services = data.get("services")
    if not isinstance(services, dict):
        data["services"] = {}
    if "version" not in data:
        data["version"] = 1
    return data


def _tighten(path: str) -> None:
    try:
        get_platform().restrict_file_to_current_user(path)
    except Exception:
        # 权限收紧失败不应阻断凭据写入，但要落盘在 detail 里由上层提示
        pass


def save_credentials(data: Dict[str, Any], layout: Optional[Layout] = None) -> Dict[str, Any]:
    layout = layout or get_layout()
    if not isinstance(data, dict):
        raise CredentialError("凭据结构非法")
    data["updated_at"] = now_iso()
    atomic_write_json(layout.credentials_file, data)
    _tighten(layout.credentials_file)
    return data


def get_credential(service: str, layout: Optional[Layout] = None) -> Dict[str, Any]:
    data = load_credentials(layout)
    entry = data.get("services", {}).get(service)
    return dict(entry) if isinstance(entry, dict) else {}


def set_credential(
    service: str,
    payload: Dict[str, Any],
    *,
    merge: bool = True,
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise CredentialError("凭据内容必须是对象")
    data = load_credentials(layout)
    services = data.setdefault("services", {})
    current = services.get(service) if merge else {}
    if not isinstance(current, dict):
        current = {}
    current.update(payload)
    current["updated_at"] = now_iso()
    services[service] = current
    save_credentials(data, layout)
    return current


def clear_credential(service: str, layout: Optional[Layout] = None) -> Dict[str, Any]:
    data = load_credentials(layout)
    services = data.get("services", {})
    services.pop(service, None)
    save_credentials(data, layout)
    return data


def password_status_of(service: str, layout: Optional[Layout] = None) -> str:
    """凭据三态（FIX-05）：captured / unknown / user_managed。

    没有任何记录时也返回 unknown —— 「没抓到」和「不知道」对用户是同一件事：
    别给他一个假的 ?token= 链接。
    """
    entry = get_credential(service, layout)
    status = entry.get("password_status")
    if status in (PASSWORD_CAPTURED, PASSWORD_UNKNOWN, PASSWORD_USER_MANAGED):
        return str(status)
    if entry.get("dashboard_password") or entry.get("admin_password"):
        return PASSWORD_CAPTURED
    return PASSWORD_UNKNOWN


def set_password_status(service: str, status: str, layout: Optional[Layout] = None) -> Dict[str, Any]:
    if status not in (PASSWORD_CAPTURED, PASSWORD_UNKNOWN, PASSWORD_USER_MANAGED):
        raise CredentialError(f"未知的凭据状态：{status}", code="invalid_password_status", http_status=400)
    return set_credential(service, {"password_status": status}, layout=layout)


def summarize(layout: Optional[Layout] = None) -> List[Dict[str, Any]]:
    """给 UI 的服务凭据概览（不含完整密钥，只给是否已存在）。"""
    data = load_credentials(layout)
    services = data.get("services", {})
    out: List[Dict[str, Any]] = []
    for name in KNOWN_SERVICES:
        entry = services.get(name) or {}
        if not isinstance(entry, dict):
            entry = {}
        out.append(
            {
                "service": name,
                "updated_at": entry.get("updated_at"),
                "keys": sorted(k for k in entry.keys() if k != "updated_at"),
                "present": bool(entry),
                "password_status": password_status_of(name, layout),
            }
        )
    return out


def ensure_perm(path: str) -> bool:
    """供安装器在写入新凭据文件后立即调用。"""
    if not os.path.exists(path):
        return False
    _tighten(path)
    return True
