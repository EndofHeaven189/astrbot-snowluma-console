"""Linux 占位实现（需求 3：必须预留扩展位）。

约定：
* 纯 stdlib 可移植的能力（路径、磁盘、可写性、PATH 查找）直接实现；
* 依赖 Windows 具体机制的能力（提权、ACL、QQ 版本、端口→进程名映射）
  一律抛 NotImplementedError，并保留与 WindowsAdapter 完全一致的签名。

后期补齐时只改本文件，业务代码不需要动。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

from core.errors import NotSupportedError
from core.platform.base import PlatformAdapter, ProcessHandle


def _not_implemented(feature: str) -> "NotSupportedError":
    return NotSupportedError(
        f"Linux 实现尚未提供：{feature}",
        hint="v0.1.0 仅完整支持 Windows；Linux 适配器需在 core/platform/linux.py 内补齐。",
        detail={"adapter": "linux", "feature": feature},
    )


class LinuxAdapter(PlatformAdapter):
    name = "linux"

    # ---------------------------------------------------------------- 路径
    def normalize_path(self, p: str) -> str:
        p = (p or "").strip().strip('"')
        p = os.path.expandvars(os.path.expanduser(p))
        return os.path.abspath(os.path.normpath(p))

    def default_root_dir(self) -> str:
        return os.path.join(os.path.expanduser("~"), "AstrBotConsole")

    # ---------------------------------------------------------------- 权限
    def is_elevated(self) -> bool:
        try:
            return os.geteuid() == 0
        except AttributeError:  # pragma: no cover
            return False

    def request_elevation(self) -> bool:
        raise _not_implemented("request_elevation")

    # ---------------------------------------------------------------- 进程
    def spawn(
        self,
        cmd: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        *,
        capture: bool = True,
        shell: bool = False,
        name: str = "",
    ) -> ProcessHandle:
        argv: List[str] = list(cmd)
        kwargs: Dict[str, object] = {
            "cwd": cwd,
            "env": env,
            "start_new_session": True,
        }
        if capture:
            kwargs.update(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        popen = subprocess.Popen(argv, **kwargs)  # type: ignore[arg-type]
        return ProcessHandle(pid=popen.pid, name=name or argv[0], cmd=argv, cwd=cwd, popen=popen)

    def kill(self, handle: ProcessHandle, force: bool = False) -> bool:
        return self.kill_by_pid(handle.pid, force=force)

    def kill_by_pid(self, pid: int, force: bool = True) -> bool:
        signal_number = 9 if force else 15
        try:
            os.kill(pid, signal_number)
            return True
        except OSError:
            return False

    def is_running(self, handle: ProcessHandle) -> bool:
        if handle.popen is not None:
            return handle.popen.poll() is None
        try:
            os.kill(handle.pid, 0)
            return True
        except OSError:
            return False

    def pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def kill_tree(self, pid: int, force: bool = True) -> bool:
        """向进程组发信号（spawn 用 start_new_session=True，组号 == 主 pid）。"""
        import signal

        signal_number = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(pid, signal_number)
            return True
        except OSError:
            try:
                os.kill(pid, signal_number)
                return True
            except OSError:
                return False

    def graceful_stop(self, pid: int) -> bool:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False

    def process_command_line(self, pid: int) -> Optional[str]:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        text = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        return text or None

    def venv_executable(self, venv_dir: str, name: str) -> Optional[str]:
        """venv 里的可执行文件；不存在返回 None（FIX-C）。"""
        path = os.path.join(venv_dir, "bin", name)
        return path if os.path.exists(path) else None

    # ---------------------------------------------------------------- 网络
    def check_port(self, port: int) -> Optional[str]:
        raise _not_implemented("check_port")

    def find_process_by_port(self, port: int) -> Optional[Tuple[int, str]]:
        raise _not_implemented("find_process_by_port")

    # ---------------------------------------------------------------- 系统信息
    def which(self, exe: str) -> Optional[str]:
        return shutil.which(exe)

    def get_app_version(self, app: str) -> Optional[str]:
        raise _not_implemented("get_app_version")

    def disk_free_gb(self, path: str) -> float:
        try:
            return shutil.disk_usage(path).free / (1024 ** 3)
        except OSError:
            return 0.0

    def is_writable(self, path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".console_write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return True
        except OSError:
            return False

    def restrict_file_to_current_user(self, path: str) -> bool:
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------- 交互
    def open_url(self, url: str) -> bool:
        raise _not_implemented("open_url")

    def open_path(self, path: str) -> bool:
        raise _not_implemented("open_path")
