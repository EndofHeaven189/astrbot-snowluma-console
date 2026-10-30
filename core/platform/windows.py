"""Windows 实现（v0.1.0 的唯一完整实现）。"""

from __future__ import annotations

import ctypes
import getpass
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Optional, Sequence, Tuple

from core.platform.base import PlatformAdapter, ProcessHandle

# Windows 创建参数
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    # ---------------------------------------------------------------- 路径
    def normalize_path(self, p: str) -> str:
        p = (p or "").strip().strip('"')
        p = os.path.expandvars(os.path.expanduser(p))
        return os.path.abspath(os.path.normpath(p))

    def default_root_dir(self) -> str:
        from core.paths import default_root_dir

        return default_root_dir()

    # ---------------------------------------------------------------- 权限
    def is_elevated(self) -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 非 Windows 或无 shell32
            return False

    def request_elevation(self) -> bool:
        """以 runas 重新拉起自身（会弹 UAC）。"""
        try:
            params = subprocess.list2cmdline(sys.argv)
            rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable, params, os.getcwd(), 1
            )
            return int(rc) > 32
        except Exception:  # pragma: no cover
            return False

    # ---------------------------------------------------------------- 进程
    def subprocess_kwargs(self) -> Dict[str, object]:
        return {
            "creationflags": CREATE_NO_WINDOW,
            "encoding": "utf-8",
            "errors": "replace",
        }

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
        argv = list(cmd)
        kwargs: Dict[str, object] = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
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
        if shell:
            # .bat / .cmd 必须经 cmd.exe 执行
            argv = ["cmd.exe", "/c", *argv]
        popen = subprocess.Popen(argv, **kwargs)  # type: ignore[arg-type]
        return ProcessHandle(
            pid=popen.pid,
            name=name or (argv[0] if argv else "process"),
            cmd=argv,
            cwd=cwd,
            popen=popen,
        )

    def kill(self, handle: ProcessHandle, force: bool = False) -> bool:
        if handle.popen is not None and handle.popen.poll() is not None:
            return True
        return self.kill_by_pid(handle.pid, force=force)

    def kill_by_pid(self, pid: int, force: bool = True) -> bool:
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.insert(1, "/F")
        code, _ = self.run_capture(args, timeout=20)
        return code == 0

    def is_running(self, handle: ProcessHandle) -> bool:
        if handle.popen is not None:
            return handle.popen.poll() is None
        return self.pid_alive(handle.pid)

    def kill_tree(self, pid: int, force: bool = True) -> bool:
        """taskkill /T：连子进程一起杀（FIX-06）。业务代码不许自己拼这条命令。"""
        if pid <= 0:
            return False
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.insert(1, "/F")
        code, _ = self.run_capture(args, timeout=20)
        return code == 0

    def graceful_stop(self, pid: int) -> bool:
        """优雅停止：CTRL_BREAK（控制台）→ taskkill 不带 /F（窗口消息）逐级降级。

        两招都可能失败（隐藏控制台的进程收不到 CTRL_BREAK、无窗口收不到 WM_CLOSE），
        失败就交给调用方在宽限期后走 kill_tree(force=True)。
        """
        if pid <= 0:
            return False
        try:
            import signal as _signal

            os.kill(pid, _signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            return True
        except (OSError, ValueError, AttributeError):
            pass
        code, _ = self.run_capture(["taskkill", "/PID", str(pid)], timeout=15)
        return code == 0

    def process_command_line(self, pid: int) -> Optional[str]:
        """读命令行。优先 PowerShell CIM，失败退回 wmic（老系统）。"""
        if pid <= 0:
            return None
        script = f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"
        code, out = self.run_capture(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=25
        )
        text = (out or "").strip()
        if code == 0 and text and "not recognized" not in text.lower():
            return text.splitlines()[0].strip()
        code, out = self.run_capture(
            ["wmic", "process", "where", f"processid={pid}", "get", "commandline"], timeout=20
        )
        if code == 0:
            for line in (out or "").splitlines():
                line = line.strip()
                if line and not line.lower().startswith("commandline"):
                    return line
        return None

    def venv_executable(self, venv_dir: str, name: str) -> str:
        """<venv>\\Scripts\\<name>.exe（FIX-01：解析集中在这里，业务代码不拼）。"""
        scripts = os.path.join(venv_dir, "Scripts")
        for candidate in (name, name + ".exe", name + ".cmd", name + ".bat"):
            path = os.path.join(scripts, candidate)
            if os.path.exists(path):
                return path
        return os.path.join(scripts, name + ".exe")

    def pid_alive(self, pid: int) -> bool:
        code, out = self.run_capture(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=15)
        return str(pid) in out

    # ---------------------------------------------------------------- 网络
    _NETSTAT_RE = re.compile(r"^\s*(TCP|UDP)\s+(\S+):(\d+)\s+(\S+)\s+(LISTENING|ESTABLISHED|\S+)?\s*(\d+)?", re.I)

    def _netstat(self) -> str:
        code, out = self.run_capture(["netstat", "-ano", "-p", "tcp"], timeout=20)
        return out if code == 0 else ""

    def find_process_by_port(self, port: int) -> Optional[Tuple[int, str]]:
        for line in self._netstat().splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local = parts[1]
            if not local.endswith(":" + str(port)):
                continue
            if parts[3].upper() != "LISTENING":
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            return pid, self.process_name(pid)
        return None

    def check_port(self, port: int) -> Optional[str]:
        found = self.find_process_by_port(port)
        return found[1] if found else None

    def process_name(self, pid: int) -> str:
        code, out = self.run_capture(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=15
        )
        if code != 0:
            return "unknown"
        first = out.strip().splitlines()[0] if out.strip() else ""
        if not first:
            return "unknown"
        return first.split(",")[0].strip('"') or "unknown"

    # ---------------------------------------------------------------- 系统信息
    def which(self, exe: str) -> Optional[str]:
        return shutil.which(exe)

    def get_app_version(self, app: str) -> Optional[str]:
        key = app.lower()
        if key in ("qq", "ntqq"):
            return self._qq_version()
        if key == "docker":
            code, out = self.run_capture(["docker", "version", "--format", "{{.Server.Version}}"], timeout=20)
            return out.strip() if code == 0 and out.strip() else None
        if key in ("python", "python3"):
            return ".".join(str(x) for x in sys.version_info[:3])
        cmd_map = {
            "node": ["node", "--version"],
            "uv": ["uv", "--version"],
            "npm": ["npm", "--version"],
            "git": ["git", "--version"],
        }
        if key in cmd_map:
            code, out = self.run_capture(cmd_map[key], timeout=20)
            if code != 0 or not out.strip():
                return None
            match = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
            return match.group(1) if match else out.strip().splitlines()[0][:60]
        return None

    def _qq_version(self) -> Optional[str]:
        """从注册表读 NTQQ 版本；只读，不改系统任何东西（Q18:A）。"""
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return None
        candidates = [
            (winreg.HKEY_CURRENT_USER, r"Software\Tencent\QQNT"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
        ]
        for hive, sub in candidates:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for value_name in ("DisplayVersion", "Version", "version"):
                        try:
                            value, _ = winreg.QueryValueEx(k, value_name)
                            if value:
                                return str(value).strip()
                        except OSError:
                            continue
            except OSError:
                continue
        # 兜底：常见安装目录里探测可执行文件
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            exe = os.path.join(base, "Tencent", "QQNT", "QQ.exe")
            if os.path.exists(exe):
                return "已安装（版本未知）"
        return None

    def disk_free_gb(self, path: str) -> float:
        try:
            target = path
            while target and not os.path.exists(target):
                parent = os.path.dirname(target)
                if parent == target:
                    break
                target = parent
            return shutil.disk_usage(target or os.getcwd()).free / (1024 ** 3)
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
        """icacls 断继承并只授予当前用户完全控制。"""
        user = os.environ.get("USERNAME") or getpass.getuser()
        code, _ = self.run_capture(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:F"], timeout=20
        )
        return code == 0

    # ---------------------------------------------------------------- 交互
    def open_url(self, url: str) -> bool:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    def open_path(self, path: str) -> bool:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
