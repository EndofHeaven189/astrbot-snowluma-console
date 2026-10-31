"""SnowLuma 首次密码、登录链接与历史目录检测（FIX-05）。"""

from __future__ import annotations

import os
import re
import secrets
from typing import Any, Dict, List, Optional

from core.constants import LOOPBACK, PASSWORD_CAPTURED, PASSWORD_UNKNOWN, PASSWORD_USER_MANAGED
from core.credential.store import get_credential, password_status_of, set_credential
from core.paths import Layout, get_layout
from core.settings import get_settings
from core.utils import now_iso

# 官方日志里的文案，兼容中英文
PASSWORD_PATTERNS = (
    re.compile(r"(?:临时密码|一次性密码|初始密码)[：:\s]*([A-Za-z0-9._~+/=-]{8,64})"),
    re.compile(r"initial (?:admin )?(?:password|credentials)[：:\s]*([A-Za-z0-9._~+/=-]{8,64})", re.I),
    re.compile(r"password[：:\s]+([A-Za-z0-9._~+/=-]{8,64})", re.I),
)

# 历史上装过 SnowLuma 的证据文件（相对 config 目录）
HISTORY_FILES = ("runtime.json",)
HISTORY_GLOB_PREFIX = "onebot_"


def ensure_admin_password(layout: Optional[Layout] = None) -> str:
    """首次启动时注入的引导密码（SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD）。

    FIX-G：注入时无法确认 SnowLuma 会不会采信这个值，所以状态统一记 unknown，
    也不渲染 ?token=。只有日志抓取（capture_from_logs / credential.capture）
    真实命中、或用户手动入库之后，才允许变成 captured / user_managed。
    """
    layout = layout or get_layout()
    entry = get_credential("snowluma", layout)
    pwd = entry.get("admin_password")
    if isinstance(pwd, str) and len(pwd) >= 10:
        return pwd
    pwd = secrets.token_hex(8)
    set_credential(
        "snowluma",
        {
            "admin_password": pwd,
            "password_source": "bootstrap-env",
            "password_status": PASSWORD_UNKNOWN,
            "password_unknown_reason": "引导密码尚未被日志确认，SnowLuma 可能忽略它",
            "generated_at": now_iso(),
        },
        layout=layout,
    )
    return pwd


def login_url(password: Optional[str] = None, layout: Optional[Layout] = None) -> Optional[str]:
    """免密登录链接。**只有密码可信时才带 ?token=**（FIX-05）。"""
    layout = layout or get_layout()
    settings = get_settings()
    port = int(settings.get("snowluma.webui_port", 5099))
    base = f"http://{LOOPBACK}:{port}"
    pwd = password
    if not pwd:
        if password_status_of("snowluma", layout) not in (PASSWORD_CAPTURED, PASSWORD_USER_MANAGED):
            return base
        entry = get_credential("snowluma", layout)
        pwd = entry.get("admin_password") if isinstance(entry.get("admin_password"), str) else None
    return f"{base}/?token={pwd}" if pwd else base


def webui_url(layout: Optional[Layout] = None) -> str:
    """不带 token 的 WebUI 地址（密码未知时的入口）。"""
    layout = layout or get_layout()
    port = int(get_settings().get("snowluma.webui_port", 5099))
    return f"http://{LOOPBACK}:{port}"


def capture_from_logs(lines: List[str], layout: Optional[Layout] = None) -> Optional[str]:
    """日志兜底：某些版本可能忽略环境变量，仍打印一次性密码。"""
    layout = layout or get_layout()
    for line in reversed(lines[-400:]):
        for pattern in PASSWORD_PATTERNS:
            match = pattern.search(line)
            if match:
                pwd = match.group(1).strip(" .,;:)]}")
                if len(pwd) < 8:
                    continue
                set_credential(
                    "snowluma",
                    {
                        "admin_password": pwd,
                        "password_source": "log-capture",
                        "password_status": PASSWORD_CAPTURED,
                        "captured_at": now_iso(),
                        "login_url": login_url(pwd, layout),
                    },
                    layout=layout,
                )
                return pwd
    return None


def sync_login_url(layout: Optional[Layout] = None) -> Optional[str]:
    url = login_url(layout=layout)
    set_credential("snowluma", {"login_url": url}, layout=layout)
    return url


# ---------------------------------------------------------------- 历史目录检测
def detect_history(layout: Optional[Layout] = None) -> Dict[str, Any]:
    """检测 instances\\SnowLuma\\config 里是否已有历史数据。

    有历史数据就说明「这不是全新安装」：SnowLuma 不会再生成一次性初始密码，
    控制台也不该假装自己知道密码（FIX-05）。
    """
    layout = layout or get_layout()
    config_dir = layout.snowluma_config_dir
    found: List[str] = []
    if os.path.isdir(config_dir):
        for name in HISTORY_FILES:
            path = os.path.join(config_dir, name)
            if os.path.exists(path):
                found.append(name)
        try:
            for name in os.listdir(config_dir):
                if name.startswith(HISTORY_GLOB_PREFIX) and name.endswith(".json"):
                    found.append(name)
        except OSError:
            pass
    data_dir = os.path.join(layout.snowluma_instance)
    has_data = bool(found) or os.path.isdir(os.path.join(data_dir, "data"))
    return {
        "history": bool(found),
        "files": sorted(set(found)),
        "config_dir": config_dir,
        "has_data": has_data,
        "password_status": password_status_of("snowluma", layout),
        "message": (
            "检测到历史数据目录：SnowLuma 不会重新生成初始密码，需要你自己在面板里重置，或换一个全新的数据目录。"
            if found
            else ""
        ),
    }
