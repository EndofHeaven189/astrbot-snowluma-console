"""日志总线。

* 每个服务一个环形缓冲（默认 5000 行，对应需求 6.6）
* 支持多路 SSE 订阅，前端断开自动回收
* 写盘：<root>\\logs\\<service>.log，超过 20MB 轮转一次
* 线程安全：子进程读取线程可以直接调用 append()
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from typing import AsyncIterator, Deque, Dict, List, Optional, Set

from core.constants import LOG_BUFFER_LINES, LOG_MAX_BYTES, LOG_MAX_FILES, LOG_RING_LINES
from core.utils import strip_ansi


class LogHub:
    def __init__(self, buffer_size: int = LOG_BUFFER_LINES) -> None:
        self._buffer_size = buffer_size
        self._buffers: Dict[str, Deque[str]] = {}
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._log_dir: Optional[str] = None

    # ---------------------------------------------------------------- 生命周期
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_log_dir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
        self._log_dir = path

    # ---------------------------------------------------------------- 写入
    def append(self, service: str, line: str) -> None:
        text = strip_ansi(line.rstrip("\r\n"))
        with self._lock:
            buf = self._buffers.setdefault(service, deque(maxlen=self._buffer_size))
            buf.append(text)
            subscribers = list(self._subscribers.get(service, ()))
            subscribers_all = list(self._subscribers.get("*", ()))
        self._write_file(service, text)
        for queue in subscribers + subscribers_all:
            self._deliver(queue, {"type": "log", "service": service, "line": text})

    def append_many(self, service: str, lines: List[str]) -> None:
        for line in lines:
            self.append(service, line)

    def _deliver(self, queue: "asyncio.Queue", payload: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._put_nowait, queue, payload)
        except RuntimeError:
            pass

    @staticmethod
    def _put_nowait(queue: "asyncio.Queue", payload: dict) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # 丢弃最旧的一行，保证不阻塞
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _write_file(self, service: str, text: str) -> None:
        if not self._log_dir:
            return
        path = os.path.join(self._log_dir, f"{service}.log")
        try:
            if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
                self._rotate(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    @staticmethod
    def _rotate(path: str) -> None:
        """<service>.log → .log.1 → .log.2 …（最多 LOG_MAX_FILES 份）。"""
        for index in range(LOG_MAX_FILES - 1, 0, -1):
            older = f"{path}.{index}"
            newer = f"{path}.{index + 1}"
            if os.path.exists(older):
                if index + 1 > LOG_MAX_FILES - 1:
                    os.remove(older)
                else:
                    os.replace(older, newer)
        os.replace(path, path + ".1")

    # ---------------------------------------------------------------- 读取
    def snapshot(self, service: str, limit: int = 0) -> List[str]:
        with self._lock:
            buf = self._buffers.get(service)
            if not buf:
                return []
            lines = list(buf)
        return lines[-limit:] if limit and limit > 0 else lines

    def ring(self, service: str, limit: int = LOG_RING_LINES) -> List[str]:
        """凭据抓取专用：内存环形缓冲最近 N 行（FIX-04，不读盘、不受轮转影响）。"""
        return self.snapshot(service, limit)

    def clear(self, service: str) -> None:
        with self._lock:
            self._buffers[service] = deque(maxlen=self._buffer_size)

    def file_path(self, service: str) -> Optional[str]:
        if not self._log_dir:
            return None
        return os.path.join(self._log_dir, f"{service}.log")

    # ---------------------------------------------------------------- 订阅
    def subscribe(self, service: str) -> "asyncio.Queue":
        queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
        with self._lock:
            self._subscribers.setdefault(service, set()).add(queue)
        return queue

    def unsubscribe(self, service: str, queue: "asyncio.Queue") -> None:
        with self._lock:
            bucket = self._subscribers.get(service)
            if bucket:
                bucket.discard(queue)
                if not bucket:
                    self._subscribers.pop(service, None)

    async def stream(self, service: str, tail: int = 200) -> AsyncIterator[dict]:
        """先补发缓冲里的历史行，再持续产出新行。"""
        queue = self.subscribe(service)
        try:
            backlog = self.snapshot(service, tail)
            for line in backlog:
                yield {"type": "log", "service": service, "line": line}
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"type": "ping"}
                    continue
                yield payload
        finally:
            self.unsubscribe(service, queue)


_hub: Optional[LogHub] = None


def get_loghub() -> LogHub:
    global _hub
    if _hub is None:
        _hub = LogHub()
        from core.paths import get_layout

        try:
            _hub.set_log_dir(get_layout().logs)
        except Exception:
            pass
    return _hub
