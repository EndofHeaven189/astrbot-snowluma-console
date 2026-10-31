"""环境检测（需求 6.1）。

检测项按当前部署路线动态显示：
- 通用：磁盘、目录可写、端口、网络、NTQQ、已装版本 vs 最新 Release
- Docker 路线：docker、compose v2、镜像可拉取
- uv 路线：Python 3.12+（实测 astrbot requires_python >=3.12）、uv
- SnowLuma：Node 版本（>= 24.22.1，实测自 check-node-version.cjs）

未选择路线时，路线相关项返回 pending（UI 显示「待选择」）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.constants import (
    APP_VERSION,
    ASTRBOT_DOCKER_IMAGE,
    ASTRBOT_REPO,
    DEPLOY_DOCKER,
    DEPLOY_UV,
    LOOPBACK,
    MIN_DISK_FREE_GB,
    MIN_NODE_FOR_LITE,
    MIN_PYTHON,
    PORT_ASTRBOT_DASHBOARD,
    PORT_CONSOLE,
    PORT_SNOWLUMA_HTTP,
    PORT_SNOWLUMA_WEBUI,
    PORT_SNOWLUMA_WS,
    SNOWLUMA_REPO,
)
from core.paths import Layout, get_layout
from core.platform import get_platform, scan_ports
from core.pythonenv import candidate_exes, probe_version
from core.service.factory import astrbot_uv_version, snowluma_version
from core.settings import Settings, get_settings
from core.utils import atomic_write_json, describe_exception, now_iso, parse_version, read_json, version_gt

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_PENDING = "pending"

RELEASE_CACHE_TTL = 600.0
# 检测结果整体缓存：页面刷新/多标签页不重复打网络。
DETECT_CACHE_TTL = 20.0
_CACHE: Dict[str, Any] = {"at": 0.0, "route": None, "payload": None}
_CACHE_LOCK = threading.Lock()
_DETECT_LOCK: Optional[asyncio.Lock] = None


def _detect_lock() -> asyncio.Lock:
    global _DETECT_LOCK
    if _DETECT_LOCK is None:
        _DETECT_LOCK = asyncio.Lock()
    return _DETECT_LOCK


def _check(
    cid: str,
    label: str,
    status: str,
    detail: str,
    *,
    group: str = "common",
    required: bool = False,
    hint: Optional[str] = None,
    value: Any = None,
) -> Dict[str, Any]:
    return {
        "id": cid,
        "label": label,
        "group": group,
        # 前端按 scope 标注「仅某路线」，与 group 同值，保留两个键避免前后端命名漂移。
        "scope": group,
        "status": status,
        "detail": detail,
        "required": required,
        "hint": hint,
        "value": value,
    }


# ---------------------------------------------------------------- 系统与运行时
def _python_candidates() -> List[str]:
    # 与安装器共用同一套发现逻辑（PATH + 官方默认安装目录），
    # 免得「检测说没 Python、安装却能找到」这种自相矛盾。
    return candidate_exes()


def _python_version(exe: str) -> Optional[Tuple[int, int, int]]:
    return probe_version(exe)


PYTHON_LABEL = f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"


def _check_python(route: Optional[str]) -> Dict[str, Any]:
    if route == DEPLOY_DOCKER:
        return _check("python", PYTHON_LABEL, STATUS_PENDING, "当前路线不需要", group="uv", value=None)
    found: list = []
    for exe in _python_candidates():
        ver = _python_version(exe)
        if ver:
            found.append((exe, ver))
    if not found:
        return _check(
            "python",
            PYTHON_LABEL,
            STATUS_FAIL if route == DEPLOY_UV else STATUS_WARN,
            "未找到可用的 Python 解释器",
            group="uv",
            required=route == DEPLOY_UV,
            hint=f"安装 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上版本并加入 PATH。",
        )
    usable = [item for item in found if item[1][:2] >= MIN_PYTHON]
    exe, ver = max(usable or found, key=lambda item: item[1])
    text = ".".join(str(x) for x in ver)
    ok = bool(usable)
    seen = ", ".join(
        ".".join(str(x) for x in item[1])
        for item in sorted(found, key=lambda i: i[1], reverse=True)
    )
    if ok:
        detail = f"Python {text}（{exe}）"
    else:
        # AstrBot 4.29.3 在 PyPI 上要求 >=3.12，低版本装不上。
        detail = f"最高 {text}，低于 AstrBot 要求的 {MIN_PYTHON[0]}.{MIN_PYTHON[1]}（{exe}）"
    return _check(
        "python",
        PYTHON_LABEL,
        STATUS_OK if ok else STATUS_FAIL,
        detail,
        group="uv",
        required=route == DEPLOY_UV,
        hint=None
        if ok
        else (
            f"AstrBot 需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上；本机已找到的版本：{seen}。"
            "安装新版本后点“重新检测”。"
        ),
        value={"path": exe, "version": text},
    )


def _check_uv(route: Optional[str]) -> Dict[str, Any]:
    if route == DEPLOY_DOCKER:
        return _check("uv", "uv 包管理器", STATUS_PENDING, "当前路线不需要", group="uv")
    found = get_platform().which("uv")
    if not found:
        return _check(
            "uv",
            "uv 包管理器",
            STATUS_WARN if route == DEPLOY_UV else STATUS_PENDING,
            "未安装 uv（可选择 pip 方式创建虚拟环境）",
            group="uv",
            required=False,
            hint="可选：winget install astral-sh.uv，或直接使用 pip。",
            value=None,
        )
    code, out = get_platform().run_capture([found, "--version"], timeout=20)
    ver = out.strip().splitlines()[0] if code == 0 and out.strip() else "uv"
    return _check("uv", "uv 包管理器", STATUS_OK, ver, group="uv", value={"path": found, "version": ver})


def _check_docker(route: Optional[str]) -> Dict[str, Any]:
    if route == DEPLOY_UV:
        return _check("docker", "Docker 引擎", STATUS_PENDING, "当前路线不需要", group="docker")
    found = get_platform().which("docker")
    if not found:
        return _check(
            "docker",
            "Docker 引擎",
            STATUS_FAIL if route == DEPLOY_DOCKER else STATUS_PENDING,
            "未找到 docker 命令",
            group="docker",
            required=route == DEPLOY_DOCKER,
            hint="安装 Docker Desktop 并启动，确保 docker 在 PATH 中。",
        )
    code, out = get_platform().run_capture([found, "version", "--format", "{{.Server.Version}}"], timeout=25)
    if code != 0:
        return _check(
            "docker",
            "Docker 引擎",
            STATUS_FAIL,
            "docker 命令存在但引擎未运行",
            group="docker",
            required=route == DEPLOY_DOCKER,
            hint="启动 Docker Desktop，等待引擎就绪。",
            value={"path": found},
        )
    return _check(
        "docker",
        "Docker 引擎",
        STATUS_OK,
        f"Docker {out.strip()}",
        group="docker",
        value={"path": found, "version": out.strip()},
    )


def _check_compose(route: Optional[str]) -> Dict[str, Any]:
    if route == DEPLOY_UV:
        return _check("compose", "Docker Compose v2", STATUS_PENDING, "当前路线不需要", group="docker")
    code, out = get_platform().run_capture(["docker", "compose", "version", "--short"], timeout=20)
    if code == 0 and out.strip():
        return _check("compose", "Docker Compose v2", STATUS_OK, f"v{out.strip()}", group="docker", value=out.strip())
    if "is not a docker command" in out or code != 0:
        code2, out2 = get_platform().run_capture(["docker-compose", "version", "--short"], timeout=20)
        if code2 == 0 and out2.strip():
            return _check(
                "compose",
                "Docker Compose v2",
                STATUS_WARN,
                f"仅检测到 standalone docker-compose {out2.strip()}",
                group="docker",
                hint="建议升级到 Docker Desktop 自带的 compose v2。",
            )
    return _check(
        "compose",
        "Docker Compose v2",
        STATUS_FAIL if route == DEPLOY_DOCKER else STATUS_PENDING,
        "未检测到 docker compose",
        group="docker",
        required=route == DEPLOY_DOCKER,
        hint="Docker Desktop 默认自带 compose v2，请确认已安装。",
    )


def _check_image(route: Optional[str]) -> Dict[str, Any]:
    if route != DEPLOY_DOCKER:
        return _check("image", "AstrBot 镜像可拉取", STATUS_PENDING, "当前路线不需要", group="docker")
    code, out = get_platform().run_capture(["docker", "manifest", "inspect", ASTRBOT_DOCKER_IMAGE], timeout=45)
    if code == 0:
        return _check("image", "AstrBot 镜像可拉取", STATUS_OK, f"{ASTRBOT_DOCKER_IMAGE} 可访问", group="docker")
    if "unauthorized" in out.lower() or "denied" in out.lower():
        return _check(
            "image",
            "AstrBot 镜像可拉取",
            STATUS_WARN,
            "镜像仓库要求登录或已限流",
            group="docker",
            hint="可先执行 docker login，或在设置里开启国内镜像地址。",
        )
    return _check(
        "image",
        "AstrBot 镜像可拉取",
        STATUS_FAIL,
        "无法访问镜像仓库",
        group="docker",
        hint="检查网络或代理设置；也可在设置里开启国内镜像。",
        value=out.strip()[:300],
    )


def _check_node() -> Dict[str, Any]:
    # full 包自带 Node 运行时，只有 lite 包才依赖本机 Node（见 SnowLuma 发布包 engines 字段）。
    variant = str(get_settings().get("snowluma.variant", "lite") or "lite")
    found = get_platform().which("node")
    if not found:
        if variant == "full":
            return _check(
                "node",
                f"Node.js >= {MIN_NODE_FOR_LITE[0]}.{MIN_NODE_FOR_LITE[1]}.1",
                STATUS_PENDING,
                "当前选择 full 包（自带运行时），本机无需 Node.js",
                group="snowluma",
                required=False,
                hint="换成 lite 包才需要本机安装 Node.js 24.22.1+。",
            )
        return _check(
            "node",
            f"Node.js >= {MIN_NODE_FOR_LITE[0]}.{MIN_NODE_FOR_LITE[1]}.1",
            STATUS_FAIL,
            "未找到 node 命令",
            group="snowluma",
            required=True,
            hint="SnowLuma 的 lite 包运行时需要 Node.js 24.22.1 或更高版本。",
        )
    code, out = get_platform().run_capture([found, "-v"], timeout=20)
    text = out.strip().lstrip("v")
    parsed = parse_version(text)
    if code != 0 or not parsed:
        return _check("node", "Node.js", STATUS_WARN, "无法读取 node 版本", group="snowluma", value={"path": found})
    ok = parsed[:3] >= (MIN_NODE_FOR_LITE[0], MIN_NODE_FOR_LITE[1], 1)
    return _check(
        "node",
        f"Node.js >= {MIN_NODE_FOR_LITE[0]}.{MIN_NODE_FOR_LITE[1]}.1",
        STATUS_OK if ok else STATUS_FAIL,
        f"Node.js {text}",
        group="snowluma",
        required=True,
        hint=None if ok else "请升级 Node.js 到 24.22.1 或更高。",
        value={"path": found, "version": text},
    )


# ---------------------------------------------------------------- 资源与网络
def _check_disk(layout: Layout) -> Dict[str, Any]:
    free = get_platform().disk_free_gb(layout.root)
    ok = free >= MIN_DISK_FREE_GB
    return _check(
        "disk",
        f"磁盘剩余 >= {MIN_DISK_FREE_GB} GB",
        STATUS_OK if ok else STATUS_FAIL,
        f"剩余 {free:.1f} GB（{layout.root}）",
        required=True,
        hint=None if ok else "清理磁盘空间，或把根目录换到容量更大的分区。",
        value=round(free, 1),
    )


def _check_writable(layout: Layout) -> Dict[str, Any]:
    ok = get_platform().is_writable(layout.root)
    return _check(
        "writable",
        "目录可写",
        STATUS_OK if ok else STATUS_FAIL,
        layout.root if ok else f"无法写入 {layout.root}",
        required=True,
        hint=None if ok else "换一个可写目录，或检查安全软件是否拦截。",
    )


def _port_setting(settings: Settings, path: str, default: int) -> int:
    """读端口设置，非法值回落默认（不因为一条脏数据让整次检测崩掉）。"""
    try:
        value = int(settings.get(path, default))
    except (TypeError, ValueError):
        return int(default)
    return value if 1 <= value <= 65535 else int(default)


def _check_ports(settings: Settings) -> List[Dict[str, Any]]:
    """端口占用检测（FIX-D / FIX-BF / FIX-AI）。

    adapter.check_port() 的契约是「占用返回进程名，空闲返回 None」，这里以前却把它
    当 PortState 用（getattr(state, "in_use") 对字符串恒为 False），于是占用的端口
    也一律判「空闲」。现在统一走 core.platform.scan_ports()（正确的 PortState 实现）；
    「由本控制台占用」比的是设置里的实际 console.port，不是常量 8765。
    """
    console_port = _port_setting(settings, "console.port", PORT_CONSOLE)
    targets = [
        (console_port, "控制台", True, True),
        (
            _port_setting(settings, "astrbot.dashboard_port", PORT_ASTRBOT_DASHBOARD),
            "AstrBot 面板",
            True,
            False,
        ),
        (settings.onebot_port(), "OneBot 反向 WS", True, False),
        (
            _port_setting(settings, "snowluma.webui_port", PORT_SNOWLUMA_WEBUI),
            "SnowLuma WebUI",
            True,
            False,
        ),
        (
            _port_setting(settings, "snowluma.http_port", PORT_SNOWLUMA_HTTP),
            "SnowLuma HTTP",
            False,
            False,
        ),
        (_port_setting(settings, "snowluma.ws_port", PORT_SNOWLUMA_WS), "SnowLuma WS", False, False),
    ]
    unique: List[Tuple[int, str, bool, bool]] = []
    seen = set()
    for port, label, required, self_owned in targets:
        if port in seen or not (1 <= port <= 65535):
            continue
        seen.add(port)
        unique.append((port, label, required, self_owned))

    states = scan_ports([item[0] for item in unique])
    out: List[Dict[str, Any]] = []
    for (port, label, required, self_owned), state in zip(unique, states):
        if not state.in_use:
            out.append(
                _check(f"port_{port}", f"端口 {port}（{label}）", STATUS_OK, "空闲", required=required, value=port)
            )
        elif self_owned:
            out.append(
                _check(f"port_{port}", f"端口 {port}（{label}）", STATUS_OK, "由本控制台占用", required=True, value=port)
            )
        else:
            proc = f"{state.process}({state.pid})" if state.process else "其他进程"
            out.append(
                _check(
                    f"port_{port}",
                    f"端口 {port}（{label}）",
                    STATUS_WARN,
                    f"被 {proc} 占用",
                    required=required,
                    hint="如该端口属于本控制台管理的服务，可忽略；否则请释放端口或在设置里改端口。",
                    value=port,
                )
            )
    return out

# ---------------------------------------------------------------- 网络探测
# 注意：pypi.org/simple/ 与 registry.npmjs.org/ 的响应体可达数十 MB，
# 直接 GET 会把「网络检测」拖到几分钟（httpx 的 timeout 是按读写操作计算的）。
# 这里统一改为流式请求，只读第一段就断开。
async def _probe(url: str, timeout: float = 6.0, headers: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    import httpx

    hdrs = {"User-Agent": f"AstrBotConsole/{APP_VERSION}"}
    if headers:
        hdrs.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=hdrs) as client:
            async with client.stream("GET", url) as resp:
                async for _chunk in resp.aiter_bytes(2048):
                    break
                return True, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, describe_exception(exc)[:160]


async def _network_checks(settings: Settings) -> List[Dict[str, Any]]:
    """三个连通性探测并发执行，各自独立超时。"""
    specs = (
        ("net_github", "https://api.github.com/rate_limit", 6.0),
        ("net_pypi", "https://pypi.org/simple/", 8.0),
        ("net_npm", "https://registry.npmjs.org/", 8.0),
    )
    outcomes = await asyncio.gather(
        *[_probe(url, timeout=budget) for _cid, url, budget in specs], return_exceptions=True
    )
    ok_map: Dict[str, Tuple[bool, str]] = {}
    for (cid, _url, _b), outcome in zip(specs, outcomes):
        if isinstance(outcome, BaseException):
            ok_map[cid] = (False, describe_exception(outcome)[:160])
        else:
            ok_map[cid] = outcome

    github = ok_map["net_github"]
    pypi = ok_map["net_pypi"]
    npm = ok_map["net_npm"]
    return [
        _check(
            "net_github",
            "访问 GitHub",
            STATUS_OK if github[0] else STATUS_FAIL,
            github[1] if github[0] else "无法连接 api.github.com",
            required=True,
            hint=None if github[0] else "安装包从 GitHub Releases 下载；如网络受限，请配置代理或使用离线包。",
        ),
        _check(
            "net_pypi",
            "访问 PyPI",
            STATUS_OK if pypi[0] else STATUS_WARN,
            pypi[1] if pypi[0] else "无法直连 PyPI",
            required=False,
            hint=None if pypi[0] else "可在设置里开启国内镜像（清华源）以加速依赖安装。",
        ),
        _check(
            "net_npm",
            "访问 npm registry",
            STATUS_OK if npm[0] else STATUS_WARN,
            npm[1] if npm[0] else "无法直连 npm registry",
            required=False,
            hint=None if npm[0] else "仅前端构建需要；控制台已内置构建产物时无需联网。",
        ),
    ]


def _check_ntqq() -> Dict[str, Any]:
    ver = get_platform().get_app_version("NTQQ")
    if ver:
        return _check("ntqq", "QQ（NTQQ）已安装", STATUS_OK, ver, value=ver)
    return _check(
        "ntqq",
        "QQ（NTQQ）已安装",
        STATUS_WARN,
        "未检测到 NTQQ",
        required=False,
        hint="SnowLuma 需要 NTQQ 客户端登录，请先安装并登录一次。",
    )


# ---------------------------------------------------------------- 版本与 Release
def _read_release_cache(layout: Layout) -> Dict[str, Any]:
    data = read_json(layout.detect_cache_file, {}) or {}
    return data if isinstance(data, dict) else {}


def latest_release(repo: str, *, layout: Optional[Layout] = None, force: bool = False, timeout: float = 6.0) -> Dict[str, Any]:
    """查最新 Release，带 10 分钟磁盘缓存，避免触发 GitHub 限流。"""
    layout = layout or get_layout()
    now = time.time()
    with _CACHE_LOCK:
        cache = _read_release_cache(layout)
        entry = cache.get(repo)
        if not force and isinstance(entry, dict) and now - float(entry.get("fetched_at", 0)) < RELEASE_CACHE_TTL:
            return entry
    import httpx

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"AstrBotConsole/{APP_VERSION}"}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(4.0, timeout)), headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        assets = [
            {
                "name": item.get("name"),
                "size": item.get("size"),
                "url": item.get("browser_download_url"),
            }
            for item in payload.get("assets", [])
        ]
        entry = {
            "repo": repo,
            "tag": payload.get("tag_name"),
            "version": str(payload.get("tag_name", "")).lstrip("vV"),
            "name": payload.get("name"),
            "published_at": payload.get("published_at"),
            "html_url": payload.get("html_url"),
            "assets": assets,
            "fetched_at": now,
        }
    except Exception as exc:
        entry = {
            "repo": repo,
            "error": describe_exception(exc)[:200],
            "tag": None,
            "version": None,
            "assets": [],
            "fetched_at": now,
        }
    with _CACHE_LOCK:
        cache = _read_release_cache(layout)
        cache[repo] = entry
        try:
            atomic_write_json(layout.detect_cache_file, cache)
        except OSError:
            pass
    return entry


def _version_checks_for(
    layout: Layout,
    settings: Settings,
    astrbot_release: Dict[str, Any],
    snowluma_release: Dict[str, Any],
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    installed = astrbot_uv_version(layout) if settings.is_uv() else None
    release = astrbot_release
    latest = release.get("version")
    if release.get("error"):
        checks.append(
            _check("astrbot_version", "AstrBot 版本", STATUS_WARN, f"无法获取最新版本：{release['error']}", hint="稍后重试，或在 GitHub 手动查看。")
        )
    elif installed and latest:
        gt = version_gt(parse_version(latest), parse_version(installed))
        checks.append(
            _check(
                "astrbot_version",
                "AstrBot 版本",
                STATUS_WARN if gt else STATUS_OK,
                f"已装 {installed} / 最新 {latest}",
                hint="有新版本可用，可在「服务」页升级。" if gt else None,
                value={"installed": installed, "latest": latest},
            )
        )
    else:
        checks.append(
            _check(
                "astrbot_version",
                "AstrBot 版本",
                STATUS_PENDING,
                f"未安装 / 最新 {latest}" if latest else "未安装",
                value={"installed": installed, "latest": latest},
            )
        )
    sl_installed = snowluma_version(layout)
    sl_release = snowluma_release
    sl_latest = sl_release.get("version")
    if sl_release.get("error"):
        checks.append(_check("snowluma_version", "SnowLuma 版本", STATUS_WARN, f"无法获取最新版本：{sl_release['error']}"))
    elif sl_installed and sl_latest:
        gt = version_gt(parse_version(sl_latest), parse_version(sl_installed))
        checks.append(
            _check(
                "snowluma_version",
                "SnowLuma 版本",
                STATUS_WARN if gt else STATUS_OK,
                f"已装 {sl_installed} / 最新 {sl_latest}",
                hint="有新版本可用，可在「服务」页重新安装。" if gt else None,
                value={"installed": sl_installed, "latest": sl_latest},
            )
        )
    else:
        checks.append(
            _check(
                "snowluma_version",
                "SnowLuma 版本",
                STATUS_PENDING,
                f"未安装 / 最新 {sl_latest}" if sl_latest else "未安装",
                value={"installed": sl_installed, "latest": sl_latest},
            )
        )
    return checks


async def _version_checks(settings: Settings, layout: Layout, *, force: bool = False) -> List[Dict[str, Any]]:
    """两个 Release 查询并发（各自磁盘缓存 10 分钟）。"""
    astrbot_release, snowluma_release = await asyncio.gather(
        asyncio.to_thread(latest_release, ASTRBOT_REPO, layout=layout, force=force),
        asyncio.to_thread(latest_release, SNOWLUMA_REPO, layout=layout, force=force),
        return_exceptions=True,
    )
    if isinstance(astrbot_release, BaseException):
        astrbot_release = {"repo": ASTRBOT_REPO, "error": describe_exception(astrbot_release)[:160], "version": None, "assets": []}
    if isinstance(snowluma_release, BaseException):
        snowluma_release = {"repo": SNOWLUMA_REPO, "error": describe_exception(snowluma_release)[:160], "version": None, "assets": []}
    return _version_checks_for(layout, settings, astrbot_release, snowluma_release)


# ---------------------------------------------------------------- 主入口
async def _guarded(
    key: str,
    label: str,
    group: str,
    factory: Any,
    timeout: float,
) -> List[Dict[str, Any]]:
    """单个检测项的独立超时保护：任何一项卡住都不拖垮整次检测。"""
    try:
        value = await asyncio.wait_for(factory(), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        return [
            _check(
                key,
                label,
                STATUS_WARN,
                f"检测超时（超过 {timeout:.0f} 秒未返回）",
                group=group,
                hint="网络较慢时可稍后重新检测；该项不影响其余结果。",
            )
        ]
    except Exception as exc:
        return [_check(key, label, STATUS_WARN, f"检测失败：{describe_exception(exc)}"[:180], group=group)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [value]


def _detect_specs(layout: Layout, settings: Settings, route: Optional[str]) -> List[Tuple[str, str, str, Any, float]]:
    return [
        ("disk", f"磁盘剩余 >= {MIN_DISK_FREE_GB} GB", "common", lambda: asyncio.to_thread(_check_disk, layout), 6.0),
        ("writable", "目录可写", "common", lambda: asyncio.to_thread(_check_writable, layout), 6.0),
        ("python", PYTHON_LABEL, "uv", lambda: asyncio.to_thread(_check_python, route), 12.0),
        ("uv", "uv 包管理器", "uv", lambda: asyncio.to_thread(_check_uv, route), 8.0),
        ("docker", "Docker 引擎", "docker", lambda: asyncio.to_thread(_check_docker, route), 10.0),
        ("compose", "Docker Compose v2", "docker", lambda: asyncio.to_thread(_check_compose, route), 8.0),
        ("image", "AstrBot 镜像可拉取", "docker", lambda: asyncio.to_thread(_check_image, route), 20.0),
        ("node", f"Node.js >= {MIN_NODE_FOR_LITE[0]}.{MIN_NODE_FOR_LITE[1]}.1", "snowluma", lambda: asyncio.to_thread(_check_node), 8.0),
        ("ntqq", "QQ（NTQQ）已安装", "common", lambda: asyncio.to_thread(_check_ntqq), 6.0),
        ("ports", "端口占用", "common", lambda: asyncio.to_thread(_check_ports, settings), 15.0),
        ("network", "网络连通性", "common", lambda: _network_checks(settings), 12.0),
        ("versions", "版本信息", "common", lambda: _version_checks(settings, layout), 16.0),
    ]


async def detect_environment(
    *,
    force: bool = False,
    layout: Optional[Layout] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    layout = layout or get_layout()
    settings = settings or get_settings()
    layout.ensure()
    route = settings.astrbot_deploy_mode()

    started = time.time()
    if not force:
        cached = _CACHE.get("payload")
        if (
            isinstance(cached, dict)
            and _CACHE.get("route") == route
            and started - float(_CACHE.get("at", 0.0)) < DETECT_CACHE_TTL
        ):
            payload = dict(cached)
            payload["cached"] = True
            return payload

    lock = _detect_lock()
    async with lock:
        # 拿到锁后二次检查缓存：并发请求只真正检测一次。
        if not force:
            cached = _CACHE.get("payload")
            now2 = time.time()
            if (
                isinstance(cached, dict)
                and _CACHE.get("route") == route
                and now2 - float(_CACHE.get("at", 0.0)) < DETECT_CACHE_TTL
            ):
                payload = dict(cached)
                payload["cached"] = True
                return payload

        specs = _detect_specs(layout, settings, route)
        groups = await asyncio.gather(*[_guarded(key, label, group, factory, budget) for key, label, group, factory, budget in specs])

    checks: List[Dict[str, Any]] = []
    for group_items in groups:
        checks.extend(group_items)

    summary = {"ok": 0, "warn": 0, "fail": 0, "pending": 0}
    for item in checks:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    blocking = [c["id"] for c in checks if c["status"] == STATUS_FAIL and c.get("required")]
    payload = {
        "route": route,
        "checked_at": now_iso(),
        "duration_ms": int((time.time() - started) * 1000),
        "cached": False,
        "checks": checks,
        "summary": summary,
        "blocking": blocking,
        "can_install": not blocking,
        "root": layout.root,
        "app_version": APP_VERSION,
        "endpoints": {
            "astrbot_dashboard": f"http://{LOOPBACK}:{settings.get('astrbot.dashboard_port', PORT_ASTRBOT_DASHBOARD)}",
            "snowluma_webui": f"http://{LOOPBACK}:{settings.get('snowluma.webui_port', PORT_SNOWLUMA_WEBUI)}",
        },
    }
    _CACHE["at"] = time.time()
    _CACHE["route"] = route
    _CACHE["payload"] = payload
    return payload
