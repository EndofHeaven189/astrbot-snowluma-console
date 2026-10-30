"""全局常量。业务代码统一从这里取默认值，避免散落的魔法数字。"""

from __future__ import annotations

APP_NAME = "AstrBot × SnowLuma 集成控制台"
APP_SHORT = "AstrBotConsole"
APP_VERSION = "0.1.1"

# ---- 服务标识 -------------------------------------------------------------
SERVICE_ASTRBOT = "astrbot"
SERVICE_SNOWLUMA = "snowluma"
SERVICES = (SERVICE_ASTRBOT, SERVICE_SNOWLUMA)

SERVICE_LABELS = {
    SERVICE_ASTRBOT: "AstrBot",
    SERVICE_SNOWLUMA: "SnowLuma",
}

# ---- 部署路线 -------------------------------------------------------------
DEPLOY_UV = "uv"
DEPLOY_DOCKER = "docker"
DEPLOY_MODES = (DEPLOY_UV, DEPLOY_DOCKER)

# ---- 默认端口（附录：关键事实速查表） --------------------------------------
DEFAULT_CONSOLE_PORT = 8765
DEFAULT_ASTRBOT_DASHBOARD_PORT = 6185
DEFAULT_ASTRBOT_ONEBOT_PORT = 6199        # aiocqhttp 反向 WS
DEFAULT_SNOWLUMA_WEBUI_PORT = 5099
DEFAULT_SNOWLUMA_HTTP_PORT = 3000
DEFAULT_SNOWLUMA_WS_PORT = 3001

# 别名（各模块里用短名字读起来更清楚）
PORT_CONSOLE = DEFAULT_CONSOLE_PORT
PORT_DASHBOARD = DEFAULT_ASTRBOT_DASHBOARD_PORT
PORT_ASTRBOT_DASHBOARD = DEFAULT_ASTRBOT_DASHBOARD_PORT
PORT_ASTRBOT_ONEBOT = DEFAULT_ASTRBOT_ONEBOT_PORT
PORT_SNOWLUMA_WEBUI = DEFAULT_SNOWLUMA_WEBUI_PORT
PORT_SNOWLUMA_HTTP = DEFAULT_SNOWLUMA_HTTP_PORT
PORT_SNOWLUMA_WS = DEFAULT_SNOWLUMA_WS_PORT
PORT_ONEBOT = DEFAULT_ASTRBOT_ONEBOT_PORT
PORT_WEBUI = DEFAULT_SNOWLUMA_WEBUI_PORT
PORT_HTTP = DEFAULT_SNOWLUMA_HTTP_PORT
PORT_WS = DEFAULT_SNOWLUMA_WS_PORT

# 环境检测里需要探测的端口（6.1 节）
DETECT_PORTS = (
    DEFAULT_ASTRBOT_DASHBOARD_PORT,
    DEFAULT_ASTRBOT_ONEBOT_PORT,
    DEFAULT_SNOWLUMA_WEBUI_PORT,
    DEFAULT_SNOWLUMA_HTTP_PORT,
    DEFAULT_SNOWLUMA_WS_PORT,
    DEFAULT_CONSOLE_PORT,
)

# ---- 外部资源 -------------------------------------------------------------
ASTRBOT_REPO = "AstrBotDevs/AstrBot"
ASTRBOT_DOCKER_IMAGE = "soulter/astrbot:latest"
SNOWLUMA_REPO = "SnowLuma/SnowLuma"
ASTRBOT_PYPI_PACKAGE = "astrbot"

# 国内镜像前缀（可在设置里开关）
MIRROR_DOCKER_PREFIX = "m.daocloud.io/docker.io"
MIRROR_PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"
MIRROR_PIP_INDEX = MIRROR_PYPI
MIRROR_PIP_HOST = "pypi.tuna.tsinghua.edu.cn"
MIRROR_GITHUB_PROXY = ""      # 形如 https://ghproxy.example/，留空则直连
MIRROR_UV_INSTALL = ""        # uv 官方脚本国内镜像；留空则用官方地址

# ---- 环境检测阈值 ---------------------------------------------------------
MIN_DISK_FREE_GB = 5
# 实测：astrbot 4.29.3 在 PyPI 上声明 requires_python ">=3.12"（需求文档 146 行要求开发时核实），
# 低于 3.12 时 pip 会在依赖回填上耗十几分钟再失败，所以按 3.12 提前拦截。
MIN_PYTHON = (3, 12)
MIN_PYTHON_CONSOLE = (3, 10)   # 控制台自身（含 start.bat）的最低要求
MIN_NODE_FOR_LITE = (24, 22, 1)   # 实测 release.json/package.json：node ^24.22.1 || >=26.12.1

# ---- 协议确认（6.4 节） ----------------------------------------------------
EULA_ENV_FLAGS = {
    "SNOWLUMA_ACCEPT_EULA": "1",
    "SNOWLUMA_ACCEPT_PRIVACY": "1",
}

# ---- 日志 -----------------------------------------------------------------
LOG_BUFFER_LINES = 5000        # 前端保留最近 5000 行
LOG_RING_LINES = 2000          # FIX-04：凭据抓取只扫这个环形缓冲，不受落盘轮转影响
LOG_MAX_BYTES = 20 * 1024 * 1024   # 单份日志上限，超过就轮转成 <service>.log.1
LOG_MAX_FILES = 3              # 保留 .log.1/.log.2，总量 ~80MB 封顶（FIX 验收：24h ≤70MB）
STOP_GRACE_SECONDS = 10        # 优雅终止超时，之后强杀进程树

# ---- 超时分档（FIX-03：全部可在 state/settings.json 的 timeouts 段覆盖） ----
TIMEOUT_DEFAULTS = {
    "dependency_install": 600,   # 装依赖（pip install astrbot）
    "service_ready": 180,        # 首次启动 → 端口就绪
    "stop_grace": STOP_GRACE_SECONDS,
    "docker_cli": 60,            # docker inspect / ps 这类查询
    "compose_up": 300,           # docker compose up -d
    "password_capture": 180,     # FIX-04：密码抓取窗口 0~180s
}

# ---- 桥接（FIX-02） -------------------------------------------------------
DOCKER_HOST_ALIAS = "host.docker.internal"
LOOPBACK = "127.0.0.1"

# ---- 凭据状态（FIX-05） ---------------------------------------------------
PASSWORD_CAPTURED = "captured"
PASSWORD_UNKNOWN = "unknown"
PASSWORD_USER_MANAGED = "user_managed"

# ---- 目录名 ---------------------------------------------------------------
DIR_APPS = "apps"
DIR_INSTANCES = "instances"
DIR_DOWNLOADS = "downloads"
DIR_BACKUPS = "backups"
DIR_LOGS = "logs"
DIR_STATE = "state"

# ---- 新增的状态文件（v0.1.1） ---------------------------------------------
STATE_PIDS = "pids.json"
STATE_INSTALL_PROGRESS = "install_progress.json"
