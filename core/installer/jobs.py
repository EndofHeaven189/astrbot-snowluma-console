"""安装任务登记（同一时刻只允许一个安装任务）。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from core.errors import InstallError
from core.loghub import get_loghub
from core.utils import describe_exception, now_iso

INSTALLER_CHANNEL = "installer"


class Job:
    def __init__(self, job_id: str, kind: str, title: str) -> None:
        self.id = job_id
        self.kind = kind
        self.title = title
        self.state = "running"
        self.started_at = now_iso()
        self.finished_at: Optional[str] = None
        self.ok: Optional[bool] = None
        self.error: Optional[str] = None
        self.steps: List[Dict[str, Any]] = []
        self.result: Dict[str, Any] = {}
        self._started = time.time()

    def step(self, name: str, state: str, detail: str = "") -> None:
        self.steps.append({"name": name, "state": state, "detail": detail, "at": now_iso()})
        get_loghub().append(
            INSTALLER_CHANNEL,
            f"[{state}] {name}" + (f" — {detail}" if detail else ""),
        )

    def log(self, line: str) -> None:
        get_loghub().append(INSTALLER_CHANNEL, line)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round(time.time() - self._started, 1),
            "ok": self.ok,
            "error": self.error,
            "steps": self.steps,
            "result": self.result,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._active: Optional[str] = None
        self._counter = 0

    def create(self, kind: str, title: str) -> Job:
        with self._lock:
            if self._active and self._jobs.get(self._active) and self._jobs[self._active].state == "running":
                raise InstallError(
                    "已有安装任务在进行中",
                    hint="请等待当前任务结束，或在「日志」页观察进度。",
                    detail={"job": self._active},
                )
            self._counter += 1
            job_id = f"{kind}-{int(time.time())}-{self._counter}"
            job = Job(job_id, kind, title)
            self._jobs[job_id] = job
            self._active = job_id
            return job

    def finish(self, job: Job, *, ok: bool, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        with self._lock:
            job.state = "done" if ok else "failed"
            job.ok = ok
            job.error = error
            job.result = result or {}
            job.finished_at = now_iso()
            if self._active == job.id:
                self._active = None

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(self._active) if self._active else None
            return job if job and job.state == "running" else None

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j._started, reverse=True)
            return [j.to_dict() for j in jobs[:limit]]


_registry: Optional[JobRegistry] = None


def get_registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry


async def run_job(job: Job, coro) -> None:
    """把协程包进任务，异常统一记到 job 上。"""
    try:
        result = await coro
        get_registry().finish(job, ok=True, result=result or {})
    except Exception as exc:  # noqa: BLE001 - 安装失败要完整落日志
        message = describe_exception(exc)
        job.log(f"[error] {message}")
        get_registry().finish(job, ok=False, error=message)
