"""通用小工具：原子写文件、读 JSON、tail 日志、时间格式化。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


def now_iso() -> str:
    """本地时区的 ISO8601 时间戳（带偏移，便于事后追溯）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    """文件名用的时间戳：20261029-164621。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def read_json(path: str, default: Any = None) -> Any:
    """读 JSON。用 utf-8-sig：AstrBot 的 cmd_config.json 带 UTF-8 BOM，
    用 utf-8 直接 json.load 会抛 "Unexpected UTF-8 BOM"。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """原子写入：先写同目录临时文件，再 os.replace。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data: Any, indent: int = 2, *, bom: bool = False) -> None:
    """原子写 JSON。

    bom=True 时按 utf-8-sig 写（带 BOM）——AstrBot 自己的配置就是这个格式，
    改写它的文件时保持原样，避免别的工具用 utf-8 读时出问题。
    """
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text, encoding="utf-8-sig" if bom else "utf-8")


def tail_lines(path: str, limit: int = 200, max_bytes: int = 512 * 1024) -> List[str]:
    """读文件末尾若干行（不会把整个大文件读进内存）。"""
    if not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(-max_bytes, os.SEEK_END)
                fh.readline()  # 丢掉可能被截断的半行
            data = fh.read()
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        return lines[-limit:] if limit > 0 else lines
    except OSError:
        return []


def file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def describe_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def copy_tree(src: str, dst: str) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def parse_version(text: str) -> Optional[tuple]:
    """从任意文本里抠出第一个版本号，返回可比较的元组。"""
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    parts = [int(x) for x in match.groups() if x is not None]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_gt(a: Optional[tuple], b: Optional[tuple]) -> bool:
    if not a or not b:
        return False
    return a > b


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """深合并（patch 覆盖 base），返回新对象。"""
    out: Dict[str, Any] = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def chunked(items: Iterable[Any], size: int) -> Iterable[List[Any]]:
    bucket: List[Any] = []
    for item in items:
        bucket.append(item)
        if len(bucket) >= size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket
