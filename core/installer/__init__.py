"""安装器：AstrBot（uv/Docker 两条路线）与 SnowLuma。"""

from core.installer.astrbot import install_astrbot
from core.installer.jobs import JobRegistry, get_registry
from core.installer.snowluma import install_snowluma

__all__ = ["install_astrbot", "install_snowluma", "JobRegistry", "get_registry"]
