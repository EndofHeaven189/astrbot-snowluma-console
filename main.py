"""AstrBot × SnowLuma 集成控制台 —— FastAPI 入口。

用法：
    python main.py                  # 默认 127.0.0.1:8765
    python main.py --port 9000      # 覆盖端口
    uvicorn main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import routes_bridge, routes_config, routes_credential, routes_env
from api import routes_install, routes_meta, routes_service, routes_settings
from core.constants import APP_NAME, APP_VERSION, LOOPBACK
from core.errors import ConsoleError
from core.loghub import get_loghub
from core.paths import get_layout, is_frozen, web_dist_dir
from core.service import lifecycle
from core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    layout = get_layout()
    layout.ensure()
    hub = get_loghub()
    hub.bind_loop(asyncio.get_running_loop())
    hub.set_log_dir(layout.logs)
    settings = get_settings()
    settings.load()
    hub.append("console", f"[boot] {APP_NAME} {APP_VERSION} 启动，数据目录 {layout.root}")

    # FIX-07：上一次异常退出（叉掉 start.bat、任务管理器结束进程）会留下仍在跑的子进程，
    # 这里先点名，前端用 /api/service/orphans 拉取并让用户选择接管或结束。
    orphans = lifecycle.scan_orphans()
    for item in orphans:
        hub.append(
            "console",
            f"[boot] 发现上次未结束的 {item.get('service')} 进程（pid={item.get('pid')}），等待用户处理",
        )

    # atexit 兜底：uvicorn 没走到 shutdown 分支时也回收子进程。
    lifecycle.install_exit_hook()
    app.state.orphans = orphans
    try:
        yield
    finally:
        hub.append("console", "[shutdown] 控制台退出")
        try:
            lifecycle.reap_children("shutdown")
        except Exception as exc:  # noqa: BLE001
            hub.append("console", f"[shutdown] 回收子进程失败：{exc!r}")


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="AstrBot 与 SnowLuma（OneBot v11）本地统一管理控制台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{LOOPBACK}:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ConsoleError)
async def console_error_handler(request: Request, exc: ConsoleError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "code": f"http_{exc.status_code}", "message": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    get_loghub().append("console", f"[error] {request.method} {request.url.path} → {exc!r}")
    return JSONResponse(
        status_code=500,
        content={"ok": False, "code": "internal_error", "message": str(exc)},
    )


for module in (
    routes_meta,
    routes_env,
    routes_install,
    routes_service,
    routes_config,
    routes_credential,
    routes_bridge,
    routes_settings,
):
    app.include_router(module.router)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "name": APP_NAME, "version": APP_VERSION, "root": get_layout().root}


class SPAStaticFiles(StaticFiles):
    """单端口托管前端构建产物，未知路径回落到 index.html（前端路由）。"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


# 打包后 web/dist 在 PyInstaller 的解包目录里，源码运行时在项目目录下，
# 由 core.paths 统一判断，避免这里再写一份平台/打包相关的路径逻辑。
WEB_DIST = web_dist_dir()
if os.path.isdir(WEB_DIST):
    app.mount("/", SPAStaticFiles(directory=WEB_DIST, html=True), name="web")
else:

    @app.get("/")
    def placeholder() -> Dict[str, Any]:
        return {
            "ok": True,
            "message": "前端尚未构建：请在 web/ 目录执行 npm install && npm run build",
            "api": "/api/meta",
            "docs": "/docs",
        }


def _open_browser_later(host: str, port: int, delay: float = 1.5) -> None:
    """延迟打开浏览器：等 uvicorn 把端口真正监听起来再跳转。"""
    import threading
    import webbrowser

    target_host = LOOPBACK if host in ("0.0.0.0", "::") else host
    url = f"http://{target_host}:{port}/"

    def run() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, name="open-browser", daemon=True).start()


def _port_is_free(host: str, port: int) -> bool:
    """试绑一次端口判断是否可用（绑完立刻释放，不长期占用）。"""
    probe_host = LOOPBACK if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((probe_host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, wanted: int, span: int = 20) -> Optional[int]:
    """在 wanted 之后找一个能用的端口；找不到返回 None（FIX-K / FIX-AP）。

    以前找不到时返回 wanted，而调用方拿返回值与 port 比（永远不等），
    于是「附近也没有可用端口」的分支是死代码。
    """
    for candidate in range(wanted, wanted + span):
        if _port_is_free(host, candidate):
            return candidate
    return None


def _pause_on_failure(code: int) -> None:
    """双击 exe 时控制台窗口会随进程结束立刻消失，出错要留一口气让人看清报错。"""
    if not is_frozen() or os.environ.get("ASTRBOT_CONSOLE_NO_PAUSE"):
        return
    if code == 0:
        return
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
        input("启动失败，按回车键关闭窗口…")
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动后不自动打开浏览器（默认在打包运行时自动打开）",
    )
    args = parser.parse_args(argv)

    import uvicorn

    settings = get_settings()
    host = args.host or str(settings.get("console.host", LOOPBACK))
    port = args.port or int(settings.get("console.port", 8765))

    # 端口被别的进程占着时直接闪退对使用者毫无信息量：自动换一个可用端口继续跑。
    if not _port_is_free(host, port):
        fallback = _pick_port(host, port + 1)
        if fallback is None:
            print("[console] 端口 %d 已被占用，附近也没有可用端口" % port, flush=True)
            return 1
        print("[console] 端口 %d 已被占用，改用 %d" % (port, fallback), flush=True)
        port = fallback

    shown_host = LOOPBACK if host in ("0.0.0.0", "::") else host
    print("[console] 控制台地址：http://%s:%d/" % (shown_host, port), flush=True)

    # 双击 exe 启动时自动把界面打开；源码运行时只在显式要求下才开，
    # 免得开发过程中反复弹窗。
    if not args.no_browser and is_frozen():
        _open_browser_later(host, port)

    uvicorn.run(
        "main:app" if args.reload else app,
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001
        print("[console] 启动异常：%r" % (exc,), flush=True)
        exit_code = 1
    _pause_on_failure(exit_code)
    sys.exit(exit_code)
