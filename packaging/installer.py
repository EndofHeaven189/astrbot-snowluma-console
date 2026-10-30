r"""安装程序：把随 Setup.exe 一起打包的载荷解到用户选的目录。

用法：
    Setup.exe                              图形界面安装（双击即用）
    Setup.exe --silent --dir=<目录>         静默安装，装完退出码 0
    uninstall.exe --uninstall --dir=<目录>  卸载（默认保留 data 目录）
    uninstall.exe --uninstall --dir=<目录> --purge   连 data 一起删

安装目录可选，装完之后：
    <目录>\AstrBotConsole.exe    控制台本体（双击启动，会自动开浏览器）
    <目录>\_internal\...         运行时依赖（PyInstaller 解包内容）
    <目录>\data\...              运行数据：apps / instances / backups / logs / state
    <目录>\uninstall.exe         卸载程序（就是 Setup.exe 自己的副本）

data 单独一层，是为了卸载/重装程序时不碰 instances（用户的插件、数据库、
凭据都在里面）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from typing import Callable, Dict, List, Optional

APP_NAME = "AstrBotConsole"
DISPLAY_NAME = "AstrBot × SnowLuma 集成控制台"
EXE_NAME = "AstrBotConsole.exe"
UNINSTALL_NAME = "uninstall.exe"
PAYLOAD_NAME = "payload.zip"


# ------------------------------------------------------------------ 基础路径
def self_exe() -> str:
    return os.path.abspath(sys.executable)


def payload_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, PAYLOAD_NAME)


def default_install_dir() -> str:
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Programs", APP_NAME)


def log_path() -> str:
    return os.path.join(tempfile.gettempdir(), "astrbot-console-setup.log")


def log(message: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 - 窗口模式下 stdout 是 None
        pass


# ------------------------------------------------------------------ 校验
def check_target(target: str) -> Optional[str]:
    """返回错误说明，None 表示可以装。"""
    if not target or not target.strip():
        return "安装目录不能为空"
    target = os.path.abspath(target)
    drive, _ = os.path.splitdrive(target)
    if not drive:
        return "请选择带盘符的绝对路径"
    if os.path.exists(target) and not os.path.isdir(target):
        return "目标路径已被一个文件占用"
    # 已存在且不是本控制台的目录，拒绝覆盖，避免误删用户别的东西
    if os.path.isdir(target):
        entries = [n for n in os.listdir(target) if n not in (".", "..")]
        ours = EXE_NAME in entries or UNINSTALL_NAME in entries
        if entries and not ours:
            return "该目录非空且不是本控制台的安装目录，请换一个空目录"
    # 试着建一层，验证可写（Program Files 下没有管理员权限会在这里失败）
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".write-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        return f"目录不可写（{exc}）。Program Files 需要管理员权限，建议装在用户目录下"
    return None


# ------------------------------------------------------------------ 快捷方式
def _powershell() -> Optional[str]:
    return shutil.which("powershell") or shutil.which("pwsh")


def create_shortcut(lnk_path: str, exe_path: str, workdir: str) -> bool:
    """用 WScript.Shell 建 .lnk（tkinter/纯标准库没有建快捷方式的能力）。"""
    ps = _powershell()
    if not ps:
        return False
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{exe}';"
        "$s.WorkingDirectory='{cwd}';"
        "$s.IconLocation='{exe},0';"
        "$s.Description='{desc}';"
        "$s.Save()"
    ).format(
        lnk=lnk_path.replace("'", "''"),
        exe=exe_path.replace("'", "''"),
        cwd=workdir.replace("'", "''"),
        desc=DISPLAY_NAME,
    )
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def schedule_self_delete(path: str) -> None:
    """Windows 不允许删除正在运行的 exe，交给一个延迟几秒的 cmd 去删。"""
    inner = 'ping -n 3 127.0.0.1 >nul & del /f /q "' + path + '"'
    try:
        # 传整条命令行（字符串）而不是列表：列表会被 subprocess 再包一层引号，
        # cmd 收到的参数就被拆坏了（实测 del 报「文件名、目录名或卷标语法不正确」）。
        subprocess.Popen(
            "cmd.exe /c " + inner,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
    except OSError as exc:
        log(f"安排自删除失败：{exc}")


def desktop_dir() -> str:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "Desktop")


def start_menu_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def shortcut_paths() -> Dict[str, str]:
    name = DISPLAY_NAME + ".lnk"
    return {
        "desktop": os.path.join(desktop_dir(), name),
        "start_menu": os.path.join(start_menu_dir(), name),
    }


# ------------------------------------------------------------------ 安装
def _extract(archive: str, target: str, on_step: Callable[[str], None]) -> int:
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        total = len(members) or 1
        for index, member in enumerate(members, start=1):
            zf.extract(member, target)
            if index % 200 == 0 or index == total:
                on_step(f"解压文件 {index}/{total}")
    return total


def install(
    target: str,
    desktop_shortcut: bool = True,
    on_step: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    report: Callable[[str], None] = on_step or (lambda _msg: None)
    target = os.path.abspath(target)
    problem = check_target(target)
    if problem:
        raise RuntimeError(problem)

    archive = payload_path()
    if not os.path.isfile(archive):
        raise RuntimeError(f"安装包损坏：缺少 {PAYLOAD_NAME}")

    log(f"安装到 {target}")
    report("准备目录…")
    os.makedirs(target, exist_ok=True)

    report("解压程序文件…")
    count = _extract(archive, target, report)
    log(f"解压完成，{count} 项")

    # 把 Setup.exe 自己复制成卸载程序；卸载逻辑就在同一个 exe 里
    try:
        if os.path.abspath(self_exe()) != os.path.join(target, UNINSTALL_NAME):
            shutil.copy2(self_exe(), os.path.join(target, UNINSTALL_NAME))
    except OSError as exc:
        log(f"复制卸载程序失败（不影响使用）：{exc}")

    exe_path = os.path.join(target, EXE_NAME)
    shortcuts: List[str] = []
    report("创建快捷方式…")
    for key, path in shortcut_paths().items():
        if key == "desktop" and not desktop_shortcut:
            continue
        if create_shortcut(path, exe_path, target):
            shortcuts.append(path)
    log("快捷方式：" + (", ".join(shortcuts) if shortcuts else "未创建"))

    report("安装完成")
    return {
        "ok": True,
        "target": target,
        "exe": exe_path,
        "data": os.path.join(target, "data"),
        "files": count,
        "shortcuts": shortcuts,
    }


# ------------------------------------------------------------------ 卸载
def uninstall(target: str, purge: bool = False) -> Dict[str, object]:
    target = os.path.abspath(target)
    removed: List[str] = []
    if not os.path.isdir(target):
        return {"ok": True, "target": target, "note": "目录不存在，无需卸载", "removed": removed}

    for path in shortcut_paths().values():
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(path)
        except OSError:
            pass

    keep: List[str] = [] if purge else ["data"]
    for name in sorted(os.listdir(target)):
        if name in keep:
            continue
        full = os.path.join(target, name)
        if os.path.normcase(full) == os.path.normcase(self_exe()):
            # 自己正在运行，删不掉；安排延迟删除
            schedule_self_delete(full)
            removed.append(full)
            continue
        try:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
            removed.append(full)
        except OSError as exc:
            log(f"删除 {full} 失败：{exc}")

    # data 保留时目录本身也留着（里面就是用户的 instances）
    if purge:
        try:
            os.rmdir(target)
        except OSError:
            pass
    log(f"卸载完成：{target}，删除 {len(removed)} 项，保留 {'data' if keep else '无'}")
    return {"ok": True, "target": target, "removed": removed, "kept_data": not purge}


# ------------------------------------------------------------------ 图形界面
def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(DISPLAY_NAME + " 安装程序")
    # 窗口尺寸交给 tkinter 按内容自适应：写死像素在 125%/150% 缩放下会把下半截控件
    # 挤出窗口（实测「开始安装」按钮整个看不见），自适应对任何 DPI 都成立。
    root.resizable(False, False)
    wrap = 560

    dir_var = tk.StringVar(value=default_install_dir())
    status_var = tk.StringVar(value="选择安装目录后点击「开始安装」。")
    desktop_var = tk.BooleanVar(value=True)
    launch_var = tk.BooleanVar(value=True)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=DISPLAY_NAME, font=("Segoe UI", 13, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="控制台与本机已有的 AstrBot / SnowLuma 配合使用，安装过程不会改动它们的配置。",
        wraplength=wrap,
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    row = ttk.Frame(frame)
    row.pack(fill="x")
    ttk.Label(row, text="安装目录").pack(side="left")
    entry = ttk.Entry(row, textvariable=dir_var)
    entry.pack(side="left", fill="x", expand=True, padx=8)

    def pick() -> None:
        chosen = filedialog.askdirectory(title="选择安装目录", initialdir=dir_var.get() or None)
        if chosen:
            dir_var.set(os.path.normpath(chosen))

    ttk.Button(row, text="浏览…", command=pick, width=8).pack(side="left")

    hint = ttk.Label(frame, text="", foreground="#666666", wraplength=wrap, justify="left")
    hint.pack(anchor="w", pady=(6, 8))

    def refresh_hint(*_args: object) -> None:
        target = os.path.abspath(dir_var.get() or default_install_dir())
        hint.configure(text="程序装到该目录，运行数据放在 " + os.path.join(target, "data") + "（卸载时默认保留）。")

    dir_var.trace_add("write", refresh_hint)
    refresh_hint()

    ttk.Checkbutton(frame, text="创建桌面快捷方式", variable=desktop_var).pack(anchor="w")
    ttk.Checkbutton(frame, text="安装完成后立即启动控制台", variable=launch_var).pack(anchor="w", pady=(0, 8))

    bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
    bar.pack(fill="x", pady=(4, 6))
    ttk.Label(frame, textvariable=status_var, wraplength=wrap, justify="left").pack(anchor="w")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", side="bottom", pady=(10, 0))

    state = {"busy": False, "done": False}

    def step(message: str) -> None:
        status_var.set(message)
        bar.step(6)

    def finish_install() -> None:
        state["busy"] = False
        state["done"] = True
        bar.configure(value=100)
        install_btn.configure(state="normal", text="完成")
        status_var.set("安装完成，可以用桌面快捷方式或开始菜单里的图标启动。")

    def worker(target: str, desktop: bool, launch: bool) -> None:
        try:
            result = install(target, desktop_shortcut=desktop, on_step=lambda m: root.after(0, step, m))
        except Exception as exc:  # noqa: BLE001
            log("安装失败：" + repr(exc))
            root.after(0, lambda: fail(str(exc)))
            return
        log("安装结果：" + repr(result))

        def after() -> None:
            finish_install()
            if launch:
                try:
                    subprocess.Popen([str(result["exe"])], cwd=str(result["target"]))
                except OSError as exc:
                    log("启动失败：" + repr(exc))

        root.after(0, after)

    def fail(message: str) -> None:
        state["busy"] = False
        install_btn.configure(state="normal", text="开始安装")
        bar.configure(value=0)
        status_var.set("安装失败：" + message)
        messagebox.showerror("安装失败", message)

    def start() -> None:
        if state["done"]:
            root.destroy()
            return
        if state["busy"]:
            return
        target = os.path.abspath(dir_var.get() or default_install_dir())
        problem = check_target(target)
        if problem:
            messagebox.showwarning("无法安装到该目录", problem)
            return
        dir_var.set(target)
        state["busy"] = True
        bar.configure(value=0)
        install_btn.configure(state="disabled", text="安装中…")
        threading.Thread(target=worker, args=(target, desktop_var.get(), launch_var.get()), daemon=True).start()

    install_btn = ttk.Button(buttons, text="开始安装", command=start, width=14)
    install_btn.pack(side="right")
    ttk.Button(buttons, text="退出", command=root.destroy, width=10).pack(side="right", padx=8)

    entry.focus_set()
    root.mainloop()
    return 0


def run_uninstall_gui(target: str) -> int:
    """双击 uninstall.exe 时的卸载流程：先确认，再问要不要连 data 一起删。"""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        if not messagebox.askyesno(
            "卸载 " + DISPLAY_NAME,
            "将从以下目录移除控制台程序：\n" + target + "\n\n运行数据（data）默认保留。",
        ):
            return 1
        purge = messagebox.askyesno(
            "运行数据",
            "是否连同 data 一起删除？\n\n"
            "data 里是 apps / instances / logs / state（安装好的服务、插件、凭据、备份）。\n"
            "选「否」保留数据，以后重装可以直接接着用。",
        )
        report = uninstall(target, purge=purge)
        messagebox.showinfo(
            "卸载完成",
            "已移除 %d 项。\n%s"
            % (len(report.get("removed") or []), "运行数据已删除。" if purge else "运行数据已保留。"),
        )
        return 0
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ 入口
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(add_help=True, description=DISPLAY_NAME + " 安装程序")
    parser.add_argument("--silent", action="store_true", help="无界面安装")
    parser.add_argument("--dir", default=None, help="安装目录")
    parser.add_argument("--no-desktop-shortcut", action="store_true", help="不建桌面快捷方式")
    parser.add_argument("--no-launch", action="store_true", help="安装完成后不启动")
    parser.add_argument("--uninstall", action="store_true", help="卸载")
    parser.add_argument("--purge", action="store_true", help="卸载时连 data 一起删除")
    args = parser.parse_args(argv)

    # 双击卸载程序时不能弹安装界面：按自身文件名判断默认动作
    if not args.uninstall and os.path.basename(self_exe()).lower() == UNINSTALL_NAME:
        return run_uninstall_gui(os.path.dirname(self_exe()))

    if args.uninstall:
        report = uninstall(args.dir or default_install_dir(), purge=args.purge)
        print(report if args.silent else repr(report))
        return 0

    if not args.silent:
        return run_gui()

    target = os.path.abspath(args.dir or default_install_dir())
    try:
        result = install(
            target,
            desktop_shortcut=not args.no_desktop_shortcut,
            on_step=lambda m: log(m),
        )
    except Exception as exc:  # noqa: BLE001
        log("安装失败：" + repr(exc))
        return 1
    log("安装成功：" + repr(result))
    if not args.no_launch:
        try:
            subprocess.Popen([str(result["exe"])], cwd=str(result["target"]))
        except OSError as exc:
            log(f"启动失败：{exc}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - 窗口模式下没有控制台，只能落日志
        import traceback

        log("未捕获异常：\n" + traceback.format_exc())
        sys.exit(1)
