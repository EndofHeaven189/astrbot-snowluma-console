"""下载工具：断点续传 + 多次重试 + 卡死检测。"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Callable, Dict, Optional

from core.errors import InstallError
from core.utils import file_size_mb

Progress = Optional[Callable[[int, int], None]]

CHUNK = 256 * 1024
UA = "AstrBotConsole/0.1.1"


async def download_file(
    url: str,
    dest: str,
    *,
    on_progress: Progress = None,
    timeout: float = 30.0,
    stall_seconds: float = 20.0,
    attempts: int = 4,
    expected_min_bytes: int = 1024,
) -> Dict[str, Any]:
    """下载 url 到 dest，支持 Range 续传与重试。

    下载过程中数据先写入 dest + ".part"，成功后再改名为 dest，避免半成品被误用。
    stall_seconds 内没有收到任何字节就中断本次尝试，下一次尝试从断点继续。
    """
    import httpx

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    headers = {"User-Agent": UA}
    total = 0
    errors: list[str] = []

    for attempt in range(1, attempts + 1):
        done = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        req_headers = dict(headers)
        if done:
            req_headers["Range"] = f"bytes={done}-"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, read=stall_seconds),
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url, headers=req_headers) as resp:
                    if resp.status_code == 416:
                        # 服务端认为已经下载完（本地文件比远端还长），当作完成处理
                        total = done
                        break
                    if resp.status_code >= 400:
                        raise InstallError(
                            f"下载失败：HTTP {resp.status_code}",
                            hint="检查网络代理，或手动下载安装包放进 downloads 目录。",
                            detail={"url": url, "status": resp.status_code},
                        )
                    length = int(resp.headers.get("content-length") or 0)
                    if resp.status_code == 206 and done:
                        total = length + done
                        mode = "ab"
                    else:
                        if done:
                            done = 0
                        total = length
                        mode = "wb"
                    delta = 0
                    last_report = 0
                    with open(tmp, mode) as fh:
                        async for chunk in resp.aiter_bytes(CHUNK):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            delta += len(chunk)
                            if on_progress and (delta - last_report >= 1024 * 1024):
                                last_report = delta
                                on_progress(done, total)
                    if on_progress:
                        on_progress(done, total)
            size_now = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            if not total or size_now >= total:
                break
            errors.append(f"第 {attempt} 次下载中断（{size_now}/{total} 字节）")
        except InstallError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"第 {attempt} 次下载出错：{str(exc)[:160]}")
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
        if attempt < attempts:
            await asyncio.sleep(min(2.0 * attempt, 5.0))

    if not os.path.exists(tmp):
        raise InstallError(
            "下载中断",
            hint="网络不稳定时可重试，或把安装包手动放入 downloads 目录。",
            detail={"url": url, "errors": errors[-3:]},
        )

    size = os.path.getsize(tmp)
    if total and size < total:
        raise InstallError(
            f"下载不完整（{size}/{total} 字节）",
            hint="网络不稳定：可重试本步骤（会从断点继续），或手动下载安装包放进 downloads 目录。",
            detail={"url": url, "bytes": size, "expected": total, "errors": errors[-3:]},
        )
    if size < expected_min_bytes:
        os.remove(tmp)
        raise InstallError("下载内容不完整", detail={"url": url, "bytes": size})
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(tmp, dest)
    return {
        "path": dest,
        "bytes": size,
        "size_mb": round(file_size_mb(dest), 2),
        "url": url,
        "attempts": attempt,
    }
