r"""打包脚本：把控制台打成 Windows exe。

产出（都在 packaging/build/ 下）：
    dist/AstrBotConsole/AstrBotConsole.exe   绿色版：整个目录拷走即用
    payload.zip                              安装包内嵌的载荷（绿色版目录的压缩包）
    dist/Setup.exe                           安装程序：安装目录可选、建快捷方式、可卸载

用法：
    python packaging/build.py                # 全流程（前端 → 绿色版 → 安装包）
    python packaging/build.py --skip-web     # 跳过前端构建（web/dist 已是最新时）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packaging")
BUILD = os.path.join(PKG, "build")
DIST = os.path.join(BUILD, "dist")
WORK = os.path.join(BUILD, "work")
WORK_SETUP = os.path.join(BUILD, "work-setup")
PAYLOAD = os.path.join(BUILD, "payload.zip")
APP_NAME = "AstrBotConsole"

# uvicorn 的这些子模块是运行期按配置动态挑的，静态分析扫不到
UVICORN_HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.auto",
]


def log(message: str) -> None:
    print("[build] " + message, flush=True)


def run(cmd, cwd=None) -> None:
    log("$ " + " ".join(cmd))
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd or ROOT)
    if proc.returncode != 0:
        raise SystemExit("命令失败（exit %d）：%s" % (proc.returncode, " ".join(cmd)))
    log("完成，用时 %.1fs" % (time.time() - started))


def npm() -> str:
    name = "npm.cmd" if os.name == "nt" else "npm"
    found = shutil.which(name) or shutil.which("npm")
    if not found:
        raise SystemExit("找不到 npm，无法构建前端")
    return found


def build_web() -> None:
    dist = os.path.join(ROOT, "web", "dist")
    log("构建前端（vite build）")
    run([npm(), "run", "build"], cwd=os.path.join(ROOT, "web"))
    if not os.path.isdir(dist):
        raise SystemExit("前端构建失败：web/dist 不存在")


def pyinstaller_python() -> str:
    """挑一个真正装了 PyInstaller 的解释器。

    本机可能同时有多个 Python（比如 3.10 装了 PyInstaller、3.12 没装），
    直接用 sys.executable 会撞上 "No module named PyInstaller"。
    """
    candidates = [sys.executable]
    try:
        sys.path.insert(0, ROOT)
        from core.pythonenv import collect  # noqa: WPS433

        for exe, _version in collect():
            if exe and os.path.normcase(exe) not in {os.path.normcase(c) for c in candidates}:
                candidates.append(exe)
    except Exception:  # noqa: BLE001
        pass
    for exe in candidates:
        try:
            probe = subprocess.run(
                [exe, "-c", "import PyInstaller;print(PyInstaller.__version__)"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            log("打包解释器：%s（PyInstaller %s）" % (exe, (probe.stdout or "").strip()))
            return exe
    raise SystemExit("没有找到装了 PyInstaller 的 Python，请先执行：pip install pyinstaller")


def build_app() -> str:
    log("打包绿色版（PyInstaller onedir）")
    cmd = [
        pyinstaller_python(), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", APP_NAME,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", BUILD,
        # 必须用绝对路径：PyInstaller 会把相对的 --add-data 解析到 --specpath 目录下
        "--add-data", os.path.join(ROOT, "web", "dist") + os.pathsep + os.path.join("web", "dist"),
    ]
    for item in UVICORN_HIDDEN:
        cmd += ["--hidden-import", item]
    cmd += ["--collect-submodules", "uvicorn"]
    cmd += [os.path.join(ROOT, "main.py")]
    run(cmd)
    exe = os.path.join(DIST, APP_NAME, APP_NAME + ".exe")
    if not os.path.isfile(exe):
        raise SystemExit("打包失败：" + exe + " 不存在")
    return exe


def make_payload(app_dir: str) -> str:
    log("压缩载荷")
    if os.path.isfile(PAYLOAD):
        os.remove(PAYLOAD)
    count = 0
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for base, _dirs, files in os.walk(app_dir):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, app_dir)
                zf.write(full, rel)
                count += 1
    size = os.path.getsize(PAYLOAD) / (1024 * 1024)
    log("载荷 %d 个文件，%.1f MB" % (count, size))
    return PAYLOAD


def build_setup() -> str:
    log("打包安装程序（PyInstaller onefile）")
    cmd = [
        pyinstaller_python(), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Setup",
        "--distpath", DIST,
        "--workpath", WORK_SETUP,
        "--specpath", BUILD,
        "--add-data", PAYLOAD + ";.",
        os.path.join(PKG, "installer.py"),
    ]
    run(cmd)
    exe = os.path.join(DIST, "Setup.exe")
    if not os.path.isfile(exe):
        raise SystemExit("打包失败：" + exe + " 不存在")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 AstrBot 集成控制台")
    parser.add_argument("--skip-web", action="store_true", help="跳过前端构建")
    parser.add_argument("--skip-app", action="store_true", help="跳过绿色版（复用已有产物）")
    args = parser.parse_args()

    os.makedirs(BUILD, exist_ok=True)
    if not args.skip_web:
        build_web()

    app_dir = os.path.join(DIST, APP_NAME)
    if args.skip_app and os.path.isdir(app_dir):
        log("复用已有绿色版：" + app_dir)
    else:
        build_app()

    make_payload(app_dir)
    setup = build_setup()

    log("全部完成：")
    log("  绿色版：" + os.path.join(app_dir, APP_NAME + ".exe"))
    log("  安装包：" + setup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
