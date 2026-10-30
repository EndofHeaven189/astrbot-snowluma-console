"""原生进程控制器（AstrBot uv/pip 路线 + SnowLuma）。

一个实现覆盖两个服务：差异只在「命令行 + 工作目录 + 环境变量」，
这正是 5.4 节要求「ProcessController 被复用，不写两套」的落地方式。

v0.1.1（FIX-03/04/06/07）：
* 就绪判定双层：TCP 可连 + HTTP GET 返回 2xx/3xx；
* 分级超时 + 就绪前进程退出立刻失败并回传退出码；
* 密码抓取在 spawn 之前就开始（日志环形缓冲，见 core.credential.capture）；
* 停止走「优雅 → 宽限 → 杀整棵进程树」，并复核端口是否真的释放。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from core.constants import LOOPBACK, STOP_GRACE_SECONDS
from core.errors import ServiceError
from core.loghub import get_loghub
from core.platform import get_platform
from core.platform.base import ProcessHandle
from core.service import registry
from core.service.base import ServiceController, ServiceStatus
from core.utils import tail_lines

# 内存中的句柄表：控制台内部重启进程后仍能优雅停止
_HANDLES: Dict[str, ProcessHandle] = {}
_HANDLE_LOCK = threading.RLock()


class ProcessController(ServiceController):
    deploy_mode = "process"

    def __init__(
        self,
        name: str,
        *,
        command: Sequence[str],
        cwd: str,
        installed_check,
        version_getter,
        port: Optional[int] = None,
        ready_url: Optional[str] = None,
        env_extra: Optional[Dict[str, str]] = None,
        shell: bool = False,
        ready_path: str = "/",
        preflight=None,
        ready_host: str = LOOPBACK,
        bridge_port: Optional[int] = None,
    ) -> None:
        self.name = name
        self._command = list(command)
        self._cwd = cwd
        self._installed_check = installed_check
        self._version_getter = version_getter
        self._port = port
        self._ready_url = ready_url
        self._env_extra = env_extra or {}
        self._shell = shell
        self._ready_path = ready_path
        self._preflight = preflight
        self._ready_host = ready_host
        self._bridge_port = bridge_port
        self._started_at: Optional[float] = None
        self._last_exit_code: Optional[int] = None
        self._residual: List[int] = []
        self._ready_detail: Dict[str, Any] = {}

    # ---------------------------------------------------------------- 状态
    def is_installed(self) -> bool:
        try:
            return bool(self._installed_check())
        except Exception:
            return False

    def _handle(self) -> Optional[ProcessHandle]:
        with _HANDLE_LOCK:
            handle = _HANDLES.get(self.name)
        if handle is not None and (handle.popen is None or handle.popen.poll() is None):
            return handle
        entry = registry.alive_record(self.name)
        if entry:
            return ProcessHandle(
                pid=int(entry.get("pid") or 0),
                name=self.name,
                cmd=list(entry.get("cmd") or []),
                cwd=entry.get("cwd"),
            )
        return None

    def status(self) -> ServiceStatus:
        adapter = get_platform()
        installed = self.is_installed()
        handle = self._handle()
        running = False
        pid = None
        if handle is not None:
            running = adapter.is_running(handle)
            pid = handle.pid if running else None
        port_owner = None
        if self._port:
            occupying = adapter.find_process_by_port(self._port)
            if occupying and occupying[0] != pid:
                port_owner = occupying[0]
        version = None
        try:
            version = self._version_getter() if installed else None
        except Exception:
            version = None
        detail: Dict[str, Any] = {
            "cwd": self._cwd,
            "command": " ".join(self._command),
            "log": get_loghub().file_path(self.name),
            "port_owner_pid": port_owner,
        }
        if self._residual:
            detail["residual_pids"] = list(self._residual)
            detail["residual_hint"] = "进程已判定结束但端口仍被占用，可能是残留子进程。"
        if self._last_exit_code is not None and not running:
            detail["last_exit_code"] = self._last_exit_code
        if self._ready_detail:
            detail["ready"] = dict(self._ready_detail)
        return ServiceStatus(
            name=self.name,
            installed=installed,
            running=running,
            version=version,
            deploy_mode=self.deploy_mode,
            pid=pid,
            port=self._port,
            url=self._ready_url,
            detail=detail,
        )

    # ---------------------------------------------------------------- 启动
    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.update({k: v for k, v in self._env_extra.items() if v is not None})
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    async def start(self, wait: bool = False, timeout: float = 0.0) -> ServiceStatus:
        current = self.status()
        if current.running:
            return current
        if not self.is_installed():
            raise ServiceError(
                f"{self.name} 尚未安装",
                hint="请先完成安装向导。",
                detail={"service": self.name},
            )
        if not os.path.isdir(self._cwd):
            os.makedirs(self._cwd, exist_ok=True)
        if self._preflight is not None:
            self._preflight()          # 启动前的目录完整性检查，失败抛 ServiceError
        adapter = get_platform()
        hub = get_loghub()
        self._last_exit_code = None
        self._ready_detail = {}
        self._residual = []

        # FIX-04：抓取窗口必须在启动之前建立，否则「首行就是密码」的情况必然漏。
        from core.credential.capture import begin_capture

        begin_capture(self.name)

        hub.append(self.name, f"=== 启动 {self.name} ===")
        hub.append(self.name, f"$ {' '.join(self._command)}  (cwd={self._cwd})")
        try:
            handle = adapter.spawn(
                self._command,
                cwd=self._cwd,
                env=self._build_env(),
                capture=True,
                shell=self._shell,
                name=self.name,
            )
        except OSError as exc:
            hub.append(self.name, f"[启动失败] {exc}")
            raise ServiceError(
                f"无法启动 {self.name}：{exc}",
                hint="请检查安装目录是否完整、是否有执行权限。",
                detail={"service": self.name, "command": self._command},
            ) from exc
        with _HANDLE_LOCK:
            _HANDLES[self.name] = handle
        self._started_at = time.time()
        registry.record(
            self.name,
            {
                "pid": handle.pid,
                "cmd": handle.cmd,
                "cwd": self._cwd,
                "port": self._port,
                "mode": self.deploy_mode,
            },
        )
        self._start_reader(handle)
        if wait:
            ok = await self.wait_ready(timeout=timeout)
            if not ok:
                raise self._not_ready_error()
        return self.status()

    def _not_ready_error(self) -> ServiceError:
        """就绪失败时把现场打包进异常：退出码 / 端口占用方 / 最后 200 行日志。"""
        logs = self.read_log(tail=200)
        adapter = get_platform()
        owner = adapter.find_process_by_port(self._port) if self._port else None
        detail: Dict[str, Any] = {
            "service": self.name,
            "port": self._port,
            "logs": logs[-200:],
            "exit_code": self._last_exit_code,
        }
        hint = "服务启动后没有在超时时间内就绪。"
        if owner and (self._handle() is None or owner[0] != self._handle().pid):
            detail["port_owner_pid"] = owner[0]
            detail["port_owner_name"] = owner[1]
            hint = (
                f"端口 {self._port} 已被 {owner[1]}（pid={owner[0]}）占用且不是本服务，"
                "请先结束占用进程或在设置里换一个端口。"
            )
            detail["reason"] = "port_conflict"
        elif self._last_exit_code is not None:
            hint = f"服务在就绪前就退出了（exit={self._last_exit_code}），已附最后 200 行日志。"
            detail["reason"] = "exited_before_ready"
        else:
            detail["reason"] = "ready_timeout"
        return ServiceError(f"{self.name} 启动失败：{hint}", hint=hint, detail=detail)

    def _start_reader(self, handle: ProcessHandle) -> None:
        popen = handle.popen
        if popen is None or popen.stdout is None:
            return
        hub = get_loghub()
        service = self.name
        controller = self

        def _pump() -> None:
            try:
                for line in iter(popen.stdout.readline, ""):
                    if not line:
                        break
                    hub.append(service, line)
            except (ValueError, OSError):
                pass
            finally:
                try:
                    code = popen.wait(timeout=5)
                except Exception:
                    code = None
                controller._last_exit_code = code
                hub.append(service, f"=== 进程结束 (exit={code}) ===")
                with _HANDLE_LOCK:
                    current = _HANDLES.get(service)
                    if current is not None and current.pid == handle.pid:
                        _HANDLES.pop(service, None)
                registry.forget(service)
                # FIX-04：进程结束后抓到密码也照样入库（很多程序退出前才打印）
                from core.credential.capture import finish_capture

                finish_capture(service)

        threading.Thread(target=_pump, name=f"logpump-{service}", daemon=True).start()

    # ---------------------------------------------------------------- 停止
    async def stop(self, force: bool = False) -> ServiceStatus:
        adapter = get_platform()
        hub = get_loghub()
        handle = self._handle()
        if handle is None:
            self._residual = self._port_residual()
            return self.status()
        pid = handle.pid
        hub.append(self.name, f"=== 停止 {self.name} (pid={pid}) ===")
        if not force:
            try:
                adapter.graceful_stop(pid)
            except Exception:
                pass
            deadline = time.time() + STOP_GRACE_SECONDS
            while time.time() < deadline:
                if not adapter.is_running(handle):
                    break
                await asyncio.sleep(0.5)
            else:
                hub.append(self.name, f"优雅终止超时 {STOP_GRACE_SECONDS}s，强制结束进程树")
                force = True
        if force or adapter.is_running(handle):
            adapter.kill_tree(pid, force=True)
        with _HANDLE_LOCK:
            _HANDLES.pop(self.name, None)
        registry.forget(self.name)
        # FIX-06：确认端口真的释放了；没释放就是有残留子进程，留给 UI 一键清理
        for _ in range(10):
            await asyncio.sleep(0.3)
            if not self._port or not adapter.find_process_by_port(self._port):
                break
        self._residual = self._port_residual()
        if self._residual:
            hub.append(self.name, f"[残留] 端口 {self._port} 仍被 pid={self._residual} 占用")
        return self.status()

    def _port_residual(self) -> List[int]:
        if not self._port:
            return []
        occupying = get_platform().find_process_by_port(self._port)
        return [occupying[0]] if occupying else []

    def kill_residual(self) -> List[int]:
        """一键清理残留进程（FIX-06）。返回被结束的 pid。"""
        killed: List[int] = []
        adapter = get_platform()
        for pid in self._port_residual():
            if adapter.kill_tree(pid, force=True):
                killed.append(pid)
        self._residual = self._port_residual()
        if killed:
            get_loghub().append(self.name, f"[残留] 已结束 pid={killed}")
        return killed

    # ---------------------------------------------------------------- 日志
    def read_log(self, tail: int = 400) -> List[str]:
        hub = get_loghub()
        buffered = hub.snapshot(self.name)
        if buffered:
            return buffered[-tail:] if tail else buffered
        path = hub.file_path(self.name)
        if path and os.path.exists(path):
            return tail_lines(path, tail)
        return []

    # ---------------------------------------------------------------- 就绪
    def ready_url(self) -> Optional[str]:
        return self._ready_url

    def get_bridge_endpoint(self, dial_from: str = "host") -> Dict[str, Any]:
        from core.constants import LOOPBACK

        port = self._bridge_port or self._port
        host = self._ready_host or LOOPBACK
        return {
            "route": self.deploy_mode,
            "host": host,
            "port": port,
            "url": f"ws://{host}:{port}/ws" if port else None,
            "strategy": "loopback",
            "candidates": [{"host": host, "reachable": None, "via": "same-namespace"}],
            "note": "原生进程与 SnowLuma 同在本机，回环地址即可。",
        }

    async def wait_ready(self, timeout: float = 0.0) -> bool:
        """双层就绪判定（FIX-03）：TCP 可连 + HTTP GET 返回 2xx/3xx。

        没有 HTTP 地址时退化成「端口有监听」；同时并行盯着进程有没有提前退出。
        """
        if timeout and timeout > 0:
            budget = float(timeout)
        else:
            from core.settings import get_settings

            budget = get_settings().timeout("service_ready")
        adapter = get_platform()
        deadline = time.time() + budget
        host = self._ready_host
        while time.time() < deadline:
            handle = self._handle()
            if handle is not None and not adapter.is_running(handle):
                self._ready_detail = {"ok": False, "reason": "exited", "port": self._port}
                return False
            if self._port and not adapter.tcp_ready(host, self._port, timeout=1.5):
                await asyncio.sleep(0.8)
                continue
            if not self._ready_url:
                if self._port:
                    self._ready_detail = {"ok": True, "reason": "port", "port": self._port}
                    return True
                await asyncio.sleep(0.5)
                continue
            url = self._ready_url.rstrip("/") + self._ready_path
            ok, status = adapter.http_ready(url, timeout=3.0)
            if ok:
                self._ready_detail = {"ok": True, "status": status, "url": url}
                return True
            self._ready_detail = {"ok": False, "status": status, "url": url}
            await asyncio.sleep(1.0)
        return False
