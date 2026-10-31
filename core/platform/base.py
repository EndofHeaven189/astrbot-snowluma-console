"""平台抽象层接口（需求 5.1）。

业务代码只允许依赖本文件里的 PlatformAdapter；
不允许绕过本层直接判断操作系统、拼绝对盘符路径或调用裸 shell。
"""

from __future__ import annotations

from core.constants import LOOPBACK

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple


@dataclass
class ProcessHandle:
    """对一个已启动进程的抽象句柄。

    popen 只在本进程内有意义；跨进程重启后请用 pid 判断存活。
    """

    pid: int
    name: str
    cmd: List[str]
    cwd: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    popen: Optional[subprocess.Popen] = None

    def is_alive(self) -> bool:
        if self.popen is not None:
            return self.popen.poll() is None
        return True

    def to_dict(self) -> Dict[str, object]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "started_at": self.started_at,
        }


@dataclass
class PortState:
    port: int
    in_use: bool
    pid: Optional[int] = None
    process: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "port": self.port,
            "in_use": self.in_use,
            "pid": self.pid,
            "process": self.process,
        }


class PlatformAdapter(ABC):
    """所有平台相关能力的唯一入口。"""

    name: str = "abstract"

    # ---------------------------------------------------------------- 路径
    @abstractmethod
    def normalize_path(self, p: str) -> str:
        """把用户输入的路径规范化成当前平台的绝对路径。"""

    @abstractmethod
    def default_root_dir(self) -> str:
        """默认根目录（程序/数据分离的根）。"""

    # ---------------------------------------------------------------- 权限
    @abstractmethod
    def is_elevated(self) -> bool:
        """当前进程是否有管理员/root 权限。"""

    @abstractmethod
    def request_elevation(self) -> bool:
        """请求提权（重新以管理员身份拉起自身）。返回是否已发起。"""

    # ---------------------------------------------------------------- 进程
    @abstractmethod
    def spawn(
        self,
        cmd: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        *,
        capture: bool = True,
        shell: bool = False,
        name: str = "",
        graceful: bool = False,
    ) -> ProcessHandle:
        """启动一个子进程。capture=True 时 stdout/stderr 合并为管道供日志采集。

        graceful=True 表示这是「受控进程」（AstrBot / SnowLuma 这类需要优雅退出的
        长期服务）：适配器必须保证它拿到一个可以用来收信号的上下文，这样
        graceful_stop() 才真的有效，而不是每次都白等宽限期再强杀（FIX-L）。
        """

    @abstractmethod
    def kill(self, handle: ProcessHandle, force: bool = False) -> bool:
        """终止进程（必要时连同子进程树）。"""

    @abstractmethod
    def is_running(self, handle: ProcessHandle) -> bool:
        """句柄对应进程是否还活着。"""

    @abstractmethod
    def kill_by_pid(self, pid: int, force: bool = True) -> bool:
        """按 PID 终止（用于重启控制台后接管遗留进程）。"""

    @abstractmethod
    def pid_alive(self, pid: int) -> bool:
        """这个 pid 是否还活着（用于 pids.json 自检与退出回收）。"""

    @abstractmethod
    def kill_tree(self, pid: int, force: bool = True) -> bool:
        """终止整棵进程树（FIX-06：node 常拉起子进程，只杀主进程会留孤儿）。"""

    @abstractmethod
    def graceful_stop(self, pid: int) -> bool:
        """先尝试优雅停止（不强制）。返回是否已发出优雅信号。"""

    @abstractmethod
    def process_command_line(self, pid: int) -> Optional[str]:
        """读取进程命令行（启动自检判断遗留 pid 是否属于本控制台的服务）。"""

    @abstractmethod
    def venv_executable(self, venv_dir: str, name: str) -> Optional[str]:
        """虚拟环境内可执行文件的路径；**不存在就返回 None**（FIX-C）。

        统一入口：Windows 按 <name> → <name>.exe → <name>.cmd → <name>.bat 探测，
        POSIX 是 <venv>/bin/<name>。业务代码不得自己拼 Scripts / bin（FIX-01）。
        以前不管存不存在都拼一个路径返回，调用方据此以为 venv 已就绪，
        结果 uv pip install --python <不存在的解释器> 失败。
        """

    # ---------------------------------------------------------------- 网络
    @abstractmethod
    def check_port(self, port: int) -> Optional[str]:
        """端口被占用时返回占用进程名，空闲返回 None。"""

    @abstractmethod
    def find_process_by_port(self, port: int) -> Optional[Tuple[int, str]]:
        """返回 (pid, 进程名)。"""

    # ---------------------------------------------------------------- 系统信息
    @abstractmethod
    def get_app_version(self, app: str) -> Optional[str]:
        """获取外部应用版本：qq / node / python / docker / uv。"""

    @abstractmethod
    def which(self, exe: str) -> Optional[str]:
        """在 PATH 中查找可执行文件，找不到返回 None。"""

    @abstractmethod
    def disk_free_gb(self, path: str) -> float:
        """目标磁盘剩余空间（GB）。"""

    @abstractmethod
    def is_writable(self, path: str) -> bool:
        """目录是否可写（会真实写一个临时文件验证）。"""

    @abstractmethod
    def restrict_file_to_current_user(self, path: str) -> bool:
        """把文件权限收紧到「仅当前用户可读写」（凭据库要求）。"""

    # ---------------------------------------------------------------- 交互
    @abstractmethod
    def open_url(self, url: str) -> bool:
        """用系统默认浏览器打开链接。"""

    @abstractmethod
    def open_path(self, path: str) -> bool:
        """用系统文件管理器打开目录。"""

    # ---------------------------------------------------------------- 便捷封装
    def run_capture(
        self,
        cmd: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        stdin_text: Optional[str] = None,
    ) -> Tuple[int, str]:
        """跑一条命令并拿到 (returncode, 合并输出)。失败不抛异常。

        stdin_text 用来喂交互式 CLI（例如 astrbot password 会连问两次新密码）。
        """
        try:
            proc = subprocess.run(
                list(cmd),
                cwd=cwd,
                env=env,
                input=stdin_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                **self.subprocess_kwargs(),
            )
            raw = proc.stdout
            if isinstance(raw, bytes):
                text = raw.decode("utf-8", "replace")
            else:
                text = raw or ""
            return proc.returncode, text
        except FileNotFoundError:
            return 127, ""
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        except OSError as exc:
            return 126, str(exc)

    def run_stream(
        self,
        cmd: Sequence[str],
        on_line: Callable[[str], None],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> int:
        """跑命令并把输出逐行回调，用于长任务的实时进度。返回码；找不到命令返回 127。"""
        try:
            proc = subprocess.Popen(
                list(cmd),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **self.subprocess_kwargs(),
            )
        except FileNotFoundError:
            return 127
        except OSError:
            return 126
        deadline = time.monotonic() + timeout if timeout else None
        try:
            stream = proc.stdout
            if stream is None:
                proc.wait(timeout=timeout)
                return proc.returncode
            for raw in stream:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                on_line(line.rstrip("\r\n"))
                if deadline is not None and time.monotonic() > deadline:
                    proc.kill()
                    return 124
            proc.wait(timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124
        finally:
            if proc.poll() is None:
                proc.kill()

    def subprocess_kwargs(self) -> Dict[str, object]:
        """子进程通用参数，子类覆盖（例如隐藏控制台窗口）。"""
        return {}

    # ---------------------------------------------------------------- 就绪探测
    def tcp_ready(self, host: str, port: int, timeout: float = 2.0) -> bool:
        """TCP 能否连上（就绪判定的第一层）。"""
        import socket

        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def http_ready(self, url: str, timeout: float = 3.0) -> Tuple[bool, int]:
        """HTTP GET 是否返回 2xx/3xx（就绪判定的第二层）。

        返回 (是否就绪, 状态码)。连不上时状态码为 0。
        """
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "AstrBotConsole/1.1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return (200 <= int(resp.status) < 400), int(resp.status)
        except urllib.error.HTTPError as exc:
            return (200 <= int(exc.code) < 400), int(exc.code)
        except Exception:
            return False, 0

    def local_ip(self) -> str:
        """本机在局域网里的 IPv4（拿不到就回环）。

        Docker 桥接退路之一：容器访问 host.docker.internal 不通时，
        改用宿主机局域网 IP。UDP connect 不会真的发包，只是让系统选出默认出口网卡。
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        except OSError:
            return LOOPBACK
        finally:
            sock.close()

    def command_matches(self, pid: int, keywords: Sequence[str]) -> bool:
        """进程命令行里是否含任一关键字（用于识别遗留进程属于哪个服务）。"""
        line = (self.process_command_line(pid) or "").lower()
        if not line:
            return False
        return any(word.lower() in line for word in keywords if word)
