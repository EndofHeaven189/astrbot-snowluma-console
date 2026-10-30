"""SnowLuma 协议接受记录（需求 6.4）。

规则：
- UI 必须同时勾选 EULA 与隐私政策才允许写入自动注入的环境变量；
- 记录写 state/eula_acceptance.json，含时间戳与版本号，可追溯；
- 不勾选也留一条人工退路（manual），但要写清来源与时间。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.constants import EULA_ENV_FLAGS
from core.errors import CredentialError
from core.paths import Layout, get_layout
from core.utils import atomic_write_json, now_iso, read_json

AGREEMENT_KEYS = ("eula", "privacy")


def _empty_record() -> Dict[str, Any]:
    return {
        "eula": None,
        "privacy": None,
        "accepted": False,
        "accepted_at": None,
        "version": None,
        "mode": None,
        "actor": None,
        "note": None,
    }


def _read(layout: Layout) -> Dict[str, Any]:
    data = read_json(layout.eula_file, {}) or {}
    record = _empty_record()
    if isinstance(data, dict):
        record.update({k: v for k, v in data.items() if k in record})
    return record


def acceptance_status(layout: Optional[Layout] = None) -> Dict[str, Any]:
    """返回可追溯的接受记录，供 UI 与日志使用。"""
    layout = layout or get_layout()
    record = _read(layout)
    record["path"] = layout.eula_file
    record["env_flags"] = dict(EULA_ENV_FLAGS) if record.get("accepted") else {}
    record["agreements"] = list(AGREEMENT_KEYS)
    return record


def is_accepted(layout: Optional[Layout] = None) -> bool:
    layout = layout or get_layout()
    record = _read(layout)
    if not record.get("accepted"):
        return False
    return all(bool(record.get(key)) for key in AGREEMENT_KEYS)


def record_acceptance(
    version: str,
    *,
    layout: Optional[Layout] = None,
    mode: str = "checkbox",
    actor: str = "console",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """记录一次接受行为。

    mode="checkbox" 表示用户在控制台勾选了 EULA 与隐私政策；
    mode="manual"   表示用户选择人工退路（自行阅读后手动确认）。
    """
    layout = layout or get_layout()
    if mode not in ("checkbox", "manual"):
        raise CredentialError(f"未知的接受模式：{mode}", detail={"mode": mode})
    stamp = now_iso()
    record = _empty_record()
    record["accepted"] = True
    record["accepted_at"] = stamp
    record["version"] = version or "unknown"
    record["mode"] = mode
    record["actor"] = actor
    record["note"] = note
    for key in AGREEMENT_KEYS:
        record[key] = {
            "accepted": True,
            "accepted_at": stamp,
            "version": record["version"],
            "mode": mode,
        }
    atomic_write_json(layout.eula_file, record)
    return acceptance_status(layout)


def revoke_acceptance(layout: Optional[Layout] = None) -> Dict[str, Any]:
    """撤销接受记录（例如用户要求重新确认）。"""
    layout = layout or get_layout()
    if os.path.exists(layout.eula_file):
        os.remove(layout.eula_file)
    return acceptance_status(layout)


def accepted_flags(layout: Optional[Layout] = None) -> Dict[str, str]:
    """启动 SnowLuma 时注入的环境变量；未接受协议则返回空 dict。"""
    if is_accepted(layout):
        return dict(EULA_ENV_FLAGS)
    return {}
