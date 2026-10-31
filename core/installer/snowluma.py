"""SnowLuma 安装（需求 6.4）。

流程：查 Release -> 下载 zip -> 解压到 apps/SnowLuma -> 写元数据 -> 记录协议接受。
注意：SnowLuma 的 config/ 与 data/ 是相对 cwd 的，所以运行时 cwd 指到 instances/SnowLuma，
apps/SnowLuma 只放可重装的程序本体。
"""

from __future__ import annotations

import os
import shutil
import zipfile
from typing import Any, Dict, List, Optional

from core.constants import SNOWLUMA_REPO, SERVICE_SNOWLUMA
from core.credential.eula import record_acceptance
from core.detector.env import latest_release
from core.errors import InstallError
from core.installer.download import download_file
from core.installer.jobs import Job
from core.paths import Layout, get_layout
from core.settings import get_settings
from core.utils import atomic_write_json, now_iso

VARIANTS = {
    "lite": "-win-x64-lite.zip",
    "full": "-win-x64.zip",
}


def pick_asset(release: Dict[str, Any], variant: str) -> Dict[str, Any]:
    suffix = VARIANTS.get(variant, VARIANTS["lite"])
    assets: List[Dict[str, Any]] = [a for a in release.get("assets", []) if a.get("name")]
    for asset in assets:
        name = str(asset["name"])
        if name.endswith(suffix) and name.startswith("SnowLuma-v"):
            return asset
    for asset in assets:
        name = str(asset["name"])
        if "-win-x64" in name and name.endswith(".zip"):
            return asset
    raise InstallError(
        "Release 中没有找到 Windows 安装包",
        hint="可能是版本命名变化，请到 GitHub Releases 手动下载后放入 downloads 目录。",
        detail={"repo": SNOWLUMA_REPO, "variant": variant},
    )


def _ensure_snowluma_stopped(layout: Layout) -> None:
    """强制重装前确认 SnowLuma 没在运行（FIX-M）。

    以前直接在运行中 rmtree，Windows 上会删一半停一半，留下半新半旧的 apps/SnowLuma。
    """
    try:
        from core.service.factory import get_controller

        controller = get_controller(SERVICE_SNOWLUMA, layout=layout)
        if controller.status().running:
            raise InstallError(
                "SnowLuma 正在运行，无法强制重装",
                hint="请先在「服务」页停止 SnowLuma，再勾选强制重装。",
                detail={"service": SERVICE_SNOWLUMA,
                        "pid": controller.status().pid},
            )
    except InstallError:
        raise
    except Exception:  # noqa: BLE001 - 状态探测失败不阻断，真正的失败由 rmtree 报出
        pass


def _safe_extract(zip_path: str, target: str) -> int:
    count = 0
    base = os.path.abspath(target)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            dest = os.path.abspath(os.path.join(base, member.filename))
            if not dest.startswith(base + os.sep):
                raise InstallError("压缩包内包含非法路径，已中止解压", detail={"entry": member.filename})
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


async def install_snowluma(
    job: Job,
    *,
    variant: Optional[str] = None,
    force: bool = False,
    accept_eula: bool = True,
    eula_mode: str = "checkbox",
    layout: Optional[Layout] = None,
) -> Dict[str, Any]:
    layout = layout or get_layout()
    layout.ensure()
    variant = (variant or "lite").lower()
    if variant not in VARIANTS:
        raise InstallError(f"未知的安装包类型：{variant}", detail={"variant": variant})

    installed_dir = layout.snowluma_app
    if os.path.isdir(installed_dir) and os.listdir(installed_dir) and not force:
        job.step("检查现有安装", "skip", "apps/SnowLuma 已存在，如需重装请勾选强制重装")
        return {"installed": True, "skipped": True, "path": installed_dir}

    job.step("查询最新版本", "run", "调用 GitHub Releases")
    release = await __import__("asyncio").to_thread(latest_release, SNOWLUMA_REPO, layout=layout, force=True)
    if release.get("error"):
        raise InstallError(
            "无法获取 SnowLuma 最新版本",
            hint="检查网络后重试；也可手动下载 zip 放入 downloads 目录。",
            detail={"error": release["error"]},
        )
    version = release.get("version") or "unknown"
    asset = pick_asset(release, variant)
    job.step("查询最新版本", "ok", f"v{version} · {asset['name']}")

    zip_path = os.path.join(layout.downloads, str(asset["name"]))
    if os.path.exists(zip_path) and os.path.getsize(zip_path) > 1024 * 1024:
        job.step("下载安装包", "skip", f"复用已下载文件 {os.path.basename(zip_path)}")
    else:
        job.step("下载安装包", "run", str(asset["name"]))
        size_mb = round(int(asset.get("size") or 0) / (1024 * 1024), 1)

        def _progress(done: int, total: int) -> None:
            if total:
                job.log(f"  下载 {done / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                job.log(f"  下载 {done / 1048576:.1f} MB / 约 {size_mb} MB")

        official = str(asset["url"])
        urls = [official]
        proxy = ""
        try:
            proxy = get_settings().github_proxy()
        except Exception:  # noqa: BLE001 - 设置读不到时退回直连
            proxy = ""
        if proxy:
            urls.insert(0, f"{proxy}/{official}")
        last_error: Optional[InstallError] = None
        for index, candidate in enumerate(urls, start=1):
            try:
                if index > 1:
                    job.log(f"  换用第 {index} 个下载源：{candidate[:80]}")
                await download_file(candidate, zip_path, on_progress=_progress, attempts=3)
                last_error = None
                break
            except InstallError as exc:
                last_error = exc
                job.log(f"  下载源 {index} 失败：{exc.message}")
        if last_error is not None:
            raise last_error
        job.step("下载安装包", "ok", f"{os.path.getsize(zip_path) / 1048576:.1f} MB")

    job.step("解压安装包", "run", "解压到 apps/SnowLuma")
    if os.path.isdir(installed_dir):
        # FIX-M / FIX-AQ：先确认没在运行，删除失败要明确报错而不是 ignore_errors 吞掉，
        # 否则解压时继续写进半删除的目录，最后得到一个坏安装。
        _ensure_snowluma_stopped(layout)
        try:
            shutil.rmtree(installed_dir)
        except OSError as exc:
            raise InstallError(
                "无法删除旧的 apps/SnowLuma（文件可能被占用）",
                hint="请先在「服务」页停止 SnowLuma（或结束进程）后重试强制重装。",
                detail={"path": installed_dir, "error": str(exc)},
            ) from exc
    os.makedirs(installed_dir, exist_ok=True)
    files = await __import__("asyncio").to_thread(_safe_extract, zip_path, installed_dir)
    launcher = os.path.join(installed_dir, "launcher.bat")
    if not os.path.exists(launcher):
        raise InstallError(
            "解压后没有找到 launcher.bat",
            hint="安装包结构可能变化，请检查 downloads 中的压缩包内容。",
            detail={"dir": installed_dir},
        )
    job.step("解压安装包", "ok", f"{files} 个文件")

    meta = {
        "version": version,
        "variant": variant,
        "asset": asset["name"],
        "installed_at": now_iso(),
        "source": release.get("html_url"),
    }
    atomic_write_json(os.path.join(installed_dir, ".console_meta.json"), meta)
    os.makedirs(layout.snowluma_config_dir, exist_ok=True)
    job.step("写入版本信息", "ok", f"v{version} ({variant})")

    if accept_eula:
        record_acceptance(version, layout=layout, mode=eula_mode, actor="installer")
        job.step("记录协议接受", "ok", f"eula_acceptance.json · v{version} · {eula_mode}")

    return {"installed": True, "version": version, "path": installed_dir, "launcher": launcher, "variant": variant}


# ---------------------------------------------------------------- FIX-05 历史数据目录
def history_guard(layout: Optional[Layout] = None) -> Dict[str, Any]:
    """安装/启动前的前置检测（FIX-05）。

    instances/SnowLuma/config 里已经有 runtime.json 或 onebot_*.json，说明这是
    历史数据目录：SnowLuma 不会重新生成一次性初始密码，控制台也抓不到。
    必须让用户明确选择「新建全新数据目录」或「我知道密码，继续」，不能静默继续。
    """
    from core.credential.snowluma import detect_history

    return detect_history(layout)


def require_no_history(
    layout: Optional[Layout] = None, *, acknowledge: bool = False, action: str = "安装"
) -> Dict[str, Any]:
    """有历史数据且用户没有明确确认时阻断。"""
    info = history_guard(layout)
    if not info.get("history") or acknowledge:
        return info
    raise InstallError(
        f"检测到 SnowLuma 历史数据目录，{action}不会重新生成初始密码",
        code="snowluma_history",
        http_status=409,
        hint="请选择「新建全新数据目录」再装，或确认已自行在面板里重置密码后继续",
        detail={
            "config_dir": info.get("config_dir"),
            "files": info.get("files") or [],
            "password_status": info.get("password_status"),
            "options": ["fresh_data", "acknowledge"],
        },
    )


def archive_config(layout: Optional[Layout] = None) -> Optional[str]:
    """把历史 config 目录整体挪到 backups 下（新建全新数据目录）。"""
    import time

    layout = layout or get_layout()
    config_dir = layout.snowluma_config_dir
    if not os.path.isdir(config_dir):
        return None
    target = os.path.join(layout.backups, f"snowluma_config_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(layout.backups, exist_ok=True)
    shutil.move(config_dir, target)
    os.makedirs(config_dir, exist_ok=True)
    return target
