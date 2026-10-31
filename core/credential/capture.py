"""初始密码抓取（FIX-04）。

v0.1.0 的问题：等服务就绪之后才去读日志「新行」，而一次性密码往往在启动的头几行就打印完了，
于是必然抓不到。v0.1.1 改成：

1. 服务 spawn **之前**就建抓取任务（窗口 0~180s，可配置）；
2. 扫的是内存环形缓冲（最近 2000 行）+ 日志文件尾部，而不是「从那之后的新行」；
3. 进程退出后还会再扫一遍（有些程序把密码放在收尾输出里）；
4. UI 常驻三个补救入口：重新抓取 / 手动粘贴 / 走重置。

抓到的密码一律原子写进 state\\credentials.json，并记 source 与 captured_at。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

from core.constants import (
    PASSWORD_CAPTURED,
    PASSWORD_UNKNOWN,
    PASSWORD_USER_MANAGED,
    SERVICE_ASTRBOT,
    LOOPBACK,
    SERVICE_SNOWLUMA,
)
from core.credential.store import get_credential, set_credential
from core.loghub import get_loghub
from core.utils import now_iso, tail_lines

_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_WORKERS: Dict[str, threading.Thread] = {}

# 各服务的密码文案（官方输出中英文都覆盖）
PATTERNS: Dict[str, List[re.Pattern]] = {
    SERVICE_ASTRBOT: [
        re.compile(r"(?:dashboard|webui|面板)[^\n]{0,20}?(?:password|密码)[：:\s=]+([A-Za-z0-9!@#$%^&*._~+/=-]{6,64})", re.I),
        re.compile(r"(?:password|密码)[：:\s=]+([A-Za-z0-9!@#$%^&*._~+/=-]{6,64})", re.I),
        re.compile(r"username[:\s]+(\w+)[,，\s]+password[:\s]+([A-Za-z0-9!@#$%^&*._~+/=-]{6,64})", re.I),
    ],
    SERVICE_SNOWLUMA: [
        re.compile(r"(?:临时密码|一次性密码|初始密码|管理员密码)[：:\s]*([A-Za-z0-9._~+/=-]{8,64})"),
        re.compile(r"(?:initial|temporary|admin)\s+password[：:\s]*([A-Za-z0-9._~+/=-]{8,64})", re.I),
        re.compile(r"password[：:\s]+([A-Za-z0-9._~+/=-]{8,64})", re.I),
    ],
}

# 明显不是密码的命中（日志里说明性的词）
_STOPWORDS = {
    "password", "passwords", "密码", "none", "null", "empty", "required",
    "changed", "updated", "reset", "unknown", "false", "true",
}


def _candidate_lines(service: str, limit: int = 2000) -> List[str]:
    hub = get_loghub()
    lines = hub.ring(service, limit)
    path = hub.file_path(service)
    if path:
        lines = tail_lines(path, 400) + lines
    return lines


def parse_password(service: str, lines: List[str]) -> Optional[str]:
    """从日志里捞密码。倒序扫：越靠后越可能是最终生效的那个。"""
    patterns = PATTERNS.get(service, [])
    for line in reversed(lines[-2000:]):
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            value = (match.group(match.lastindex or 1) or "").strip(" .,;:)]}")
            if len(value) < 6 or value.lower() in _STOPWORDS:
                continue
            if value.lower().startswith(("http", "ws://")):
                continue
            return value
    return None


def _store(service: str, password: str, source: str, note: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "password_source": source,
        "password_status": PASSWORD_CAPTURED,
        "captured_at": now_iso(),
    }
    if service == SERVICE_SNOWLUMA:
        payload["admin_password"] = password
        payload["login_url"] = build_login_url(password)
    else:
        payload["dashboard_password"] = password
        payload["password"] = password
    if note:
        payload["capture_note"] = note
    return set_credential(service, payload)


def build_login_url(password: Optional[str]) -> Optional[str]:
    if not password:
        return None
    from core.settings import get_settings

    port = int(get_settings().get("snowluma.webui_port", 5099))
    return f"http://{LOOPBACK}:{port}/?token={password}"


def scan_once(service: str, *, store: bool = True) -> Optional[str]:
    """扫一遍当前缓冲；命中就入库。返回抓到的密码。"""
    existing = get_credential(service).get("password_status")
    password = parse_password(service, _candidate_lines(service))
    if not password:
        return None
    if existing == PASSWORD_USER_MANAGED:
        # 用户手动填过的优先，不拿日志覆盖
        return None
    _store(service, password, "log-capture")
    get_loghub().append(service, f"[凭据] 已从日志抓取到初始密码（{len(password)} 位）")
    return password


def begin_capture(service: str, timeout: float = 0.0) -> Dict[str, Any]:
    """服务启动前调用：登记抓取窗口并起后台线程。"""
    from core.settings import get_settings

    window = float(timeout) if timeout and timeout > 0 else get_settings().timeout("password_capture")
    job = {
        "service": service,
        "started_at": now_iso(),
        "deadline": time.time() + window,
        "window": window,
        "captured": False,
        "last_scan": None,
        "state": "running",
    }
    with _LOCK:
        _JOBS[service] = job
        existing = _WORKERS.get(service)
        if existing is not None and existing.is_alive():
            return dict(job)

        def _worker() -> None:
            try:
                while True:
                    with _LOCK:
                        current = _JOBS.get(service)
                    if current is None:
                        return
                    if scan_once(service):
                        with _LOCK:
                            current["captured"] = True
                            current["state"] = "captured"
                        return
                    if time.time() > current["deadline"]:
                        with _LOCK:
                            current["state"] = "timeout"
                        return
                    time.sleep(2.0)
            except Exception:  # pragma: no cover - 抓取失败不能影响服务
                # FIX-AC：闭包里的 job 是 begin_capture 的局部对象，不是 _JOBS 里那一个；
                # 直接写 job["state"] 改的是影子副本，前端轮询永远是 running。
                with _LOCK:
                    current = _JOBS.get(service)
                    if current is not None:
                        current["state"] = "error"

        thread = threading.Thread(target=_worker, name=f"capture-{service}", daemon=True)
        _WORKERS[service] = thread
        thread.start()
    return dict(job)


def finish_capture(service: str) -> None:
    """进程结束时调用：再扫最后一轮，然后收尾。"""
    try:
        scan_once(service)
    except Exception:
        pass
    with _LOCK:
        job = _JOBS.get(service)
        if job and job.get("state") == "running":
            job["state"] = "finished"


def recapture(service: str) -> Dict[str, Any]:
    """UI「重新抓取」按钮：立即扫一遍缓冲 + 文件尾部。"""
    password = scan_once(service)
    job = begin_capture(service) if not password else None
    return {
        "service": service,
        "captured": bool(password),
        "password": password,
        "status": status(service),
        "job": job,
    }


def manual(service: str, password: str, note: str = "") -> Dict[str, Any]:
    """UI「手动粘贴入库」按钮。"""
    value = (password or "").strip()
    if len(value) < 4:
        from core.errors import CredentialError

        raise CredentialError("密码太短，至少 4 位", code="invalid_password", http_status=400)
    entry = _store(service, value, "user-manual", note or "用户手动粘贴")
    set_credential(service, {"password_status": PASSWORD_USER_MANAGED})
    info = status(service)
    return {
        "service": service,
        "captured": True,
        "entry_keys": sorted(entry.keys()),
        "password_status": info.get("password_status"),
        "status": info,
    }


def mark_unknown(service: str, reason: str) -> Dict[str, Any]:
    """FIX-05：历史数据目录等场景，明确标注「不知道密码」。"""
    entry = set_credential(
        service,
        {"password_status": PASSWORD_UNKNOWN, "password_unknown_reason": reason, "marked_at": now_iso()},
    )
    return entry


def status(service: str) -> Dict[str, Any]:
    from core.credential.store import password_status_of

    job = _JOBS.get(service) or {}
    entry = get_credential(service)
    return {
        "service": service,
        "password_status": password_status_of(service),
        "password_source": entry.get("password_source"),
        "captured_at": entry.get("captured_at"),
        "has_password": bool(entry.get("dashboard_password") or entry.get("admin_password")),
        "job_state": job.get("state"),
        "job_started_at": job.get("started_at"),
        "job_window": job.get("window"),
    }
