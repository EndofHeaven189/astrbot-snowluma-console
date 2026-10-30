"""控制台自身的元信息。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from core.constants import APP_NAME, APP_VERSION, PORT_DASHBOARD, PORT_ONEBOT
from core.credential import store as credential_store
from core.credential.eula import acceptance_status
from core.paths import get_layout
from core.platform import get_platform
from core.settings import get_settings

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("")
def meta() -> Dict[str, Any]:
    layout = get_layout()
    settings = get_settings()
    adapter = get_platform()
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "platform": adapter.name if hasattr(adapter, "name") else "windows",
        "python": getattr(adapter, "python_version", None),
        "root": layout.root,
        "dirs": layout.as_dict(),
        "deploy_mode": settings.astrbot_deploy_mode(),
        "ports": {
            "console": int(settings.get("console.port", 8765)),
            "astrbot_dashboard": int(settings.get("astrbot.dashboard_port", PORT_DASHBOARD)),
            "astrbot_onebot": int(settings.get("astrbot.onebot_port", PORT_ONEBOT)),
            "snowluma_webui": int(settings.get("snowluma.webui_port", 5099)),
            "snowluma_http": int(settings.get("snowluma.http_port", 3000)),
            "snowluma_ws": int(settings.get("snowluma.ws_port", 3001)),
        },
        "eula": acceptance_status(layout),
        "credentials": credential_store.summarize(layout),
        "settings_file": layout.settings_file,
        "version_string": f"{APP_NAME} {APP_VERSION}",
    }
