"""Python 解释器发现（环境检测与安装共用）。

用户最常见的情况是：Python 装了，但没勾 "Add to PATH"。所以除了 PATH 里的
python / python3 / py 之外，这里还会扫描官方安装器的默认目录：
  %LOCALAPPDATA%\\Programs\\Python\\Python*\\python.exe
  %ProgramFiles%\\Python*\\python.exe
  %SystemDrive%\\Python*\\python.exe
以及 py 启动器（它会自己挑本机最高版本）。
"""

from __future__ import annotations

import glob
import os
import sys
from typing import List, Optional, Tuple

from core.paths import is_frozen
from core.platform import get_platform

LAUNCHER_NAMES = {"py", "py.exe", "pyw", "pyw.exe"}


def candidate_exes() -> List[str]:
    """返回所有可尝试的解释器路径（去重、保序、只留真实存在的文件）。"""
    adapter = get_platform()
    out: List[str] = []
    seen: set = set()

    def add(path: Optional[str]) -> None:
        if not path:
            return
        try:
            path = os.path.abspath(path)
        except OSError:
            return
        key = os.path.normcase(path)   # Windows 上 python.EXE 与 python.exe 是同一个文件
        if os.path.isfile(path) and key not in seen:
            seen.add(key)
            out.append(path)

    for name in ("python", "python3", "py"):
        add(adapter.which(name))

    roots: List[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs", "Python"))
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(key)
        if base:
            roots.append(base)
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        roots.append(system_drive + os.sep)

    for root in roots:
        for pattern in ("Python*/python.exe", "Python*/*/python.exe"):
            for path in sorted(glob.glob(os.path.join(root, pattern)), reverse=True):
                add(path)

    # 打包成 exe 之后 sys.executable 是控制台自己，不能当解释器候选，
    # 否则探测版本会把控制台再启动一次（还会占端口）。
    if sys.executable and not is_frozen():
        add(sys.executable)
    return out


def probe_version(exe: str) -> Optional[Tuple[int, int, int]]:
    """探测解释器版本；探测失败返回 None。

    注意必须打印点分版本号：core.utils.parse_version 用正则解析 d+.d+，
    打印成 "(3, 12, 10)" 这样的元组会让所有解释器都被判为不可用。
    """
    probe = "import sys;print('%d.%d.%d' % sys.version_info[:3])"
    if os.path.basename(exe).lower() in LAUNCHER_NAMES:
        cmd = [exe, "-3", "-c", probe]
    else:
        cmd = [exe, "-c", probe]
    code, out = get_platform().run_capture(cmd, timeout=25)
    if code != 0 or not out.strip():
        return None
    from core.utils import parse_version

    parsed = parse_version(out)
    if not parsed:
        return None
    return (parsed[0], parsed[1], parsed[2] if len(parsed) > 2 else 0)


def collect() -> List[Tuple[str, Tuple[int, int, int]]]:
    """返回 [(解释器路径, 版本)]，按版本从高到低排序。"""
    found: List[Tuple[str, Tuple[int, int, int]]] = []
    for exe in candidate_exes():
        ver = probe_version(exe)
        if ver:
            found.append((exe, ver))
    found.sort(key=lambda item: item[1], reverse=True)
    return found
