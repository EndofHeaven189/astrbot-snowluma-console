r"""平台抽象层自检（v0.1.1 验收 9）。

检查业务代码有没有绕过 PlatformAdapter 直接调系统能力。规则全部基于 AST，
只看真正的代码（调用 / 字符串常量），不误伤注释与文档字符串。

    python scripts/check_abstraction.py          # 输出 JSON，告警为 0 时退出码 0
    python scripts/check_abstraction.py --text   # 人类可读

规则：
  1. os.system(...)            全项目禁止（平台适配器也不行）
  2. platform.system()         全项目禁止（用 PlatformAdapter 的接口）
  3. subprocess.Popen / os.popen     只允许 core/platform/
  4. 盘符字面量 D:\...          禁止（路径一律由 Layout 计算）
  5. "Scripts" / "bin" 作为 venv 路径段   只允许 core/platform/
  6. "127.0.0.1" 字面量         只允许 core/constants.py（其余用 LOOPBACK）
  7. taskkill / powershell 字符串  只允许 core/platform/
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {
    "web", "node_modules", "dist", "build", "__pycache__", ".git", ".venv",
    "state", "instances", "apps", "backups", "logs", "downloads", "packaging",
}

# 这些目录里的文件自己就是平台适配器，允许直接碰系统能力
PLATFORM_DIR = os.path.join("core", "platform")

# 常量定义处允许出现回环地址字面量
ALLOW_LOOPBACK = {os.path.join("core", "constants.py")}

# 自检脚本本身、以及验收脚本里会出现这些词，不参与扫描
DEV_SCRIPTS = os.path.join("scripts")  # 回归脚本直连本机端口，不算业务代码

SKIP_FILES = {
    os.path.join("scripts", "check_abstraction.py"),
    os.path.join("scripts", "acceptance_check.py"),
}

FORBIDDEN_CALLS = {
    "os.system": "用 PlatformAdapter.run_capture / spawn 代替",
    "platform.system": "用 PlatformAdapter 的能力或 os.name 分支代替",
}
PLATFORM_ONLY_CALLS = {
    "subprocess.Popen": "进程拉起统一走 PlatformAdapter.spawn",
    "os.popen": "命令执行统一走 PlatformAdapter.run_capture",
}
PLATFORM_ONLY_STRINGS = {
    "Scripts": "venv 可执行文件用 PlatformAdapter.venv_executable 解析",
    "taskkill": "杀进程树用 PlatformAdapter.kill_tree",
    "powershell": "进程信息用 PlatformAdapter.process_command_line",
}


def _iter_py_files() -> Iterable[str]:
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, ROOT)
            if rel in SKIP_FILES:
                continue
            yield rel


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _docstring_nodes(tree: ast.AST) -> Set[int]:
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _looks_like_loopback(value: str) -> bool:
    """只把「真拿来当地址用」的字符串算违规；提示文案里提到 IP 不算。"""
    return bool(re.search(r"(?:^|//)127\.0\.0\.1", value.strip()))


def _is_drive_path(value: str) -> bool:
    return len(value) > 2 and value[1] == ":" and value[0].isalpha() and value[2] in "\\/"


def check_file(rel: str) -> List[Dict[str, Any]]:
    path = os.path.join(ROOT, rel)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            source = handle.read()
    except OSError as exc:
        return [{"file": rel, "line": 0, "rule": "read", "message": str(exc)}]

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [{"file": rel, "line": exc.lineno or 0, "rule": "syntax", "message": str(exc)}]

    in_platform = rel.startswith(PLATFORM_DIR + os.sep) or rel.startswith(PLATFORM_DIR + "/")
    docstrings = _docstring_nodes(tree)
    problems: List[Dict[str, Any]] = []

    def report(line: int, rule: str, message: str, snippet: str = "") -> None:
        problems.append({"file": rel.replace(os.sep, "/"), "line": line, "rule": rule, "message": message, "snippet": snippet})

    # FIX-AA：f-string 的文本片段挂在 ast.JoinedStr.values 下，单独遍历一遍，
    # 保证 f"http://127.0.0.1:{port}" 这类拼接也能被规则 6 命中。
    strings: List[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name and name in FORBIDDEN_CALLS:
                report(node.lineno, "forbidden_call", f"{name}() 不允许出现在业务代码：{FORBIDDEN_CALLS[name]}")
            if name and name in PLATFORM_ONLY_CALLS and not in_platform:
                report(node.lineno, "platform_only_call", f"{name}() 只能在 core/platform/ 内使用：{PLATFORM_ONLY_CALLS[name]}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    strings.append(part)

    for node in strings:
        if id(node) in docstrings:
            continue
        value = node.value
        if _is_drive_path(value) and not in_platform:
            report(node.lineno, "drive_literal", f"盘符字面量 {value!r}：路径应由 core.paths.Layout 计算")
        if _looks_like_loopback(value) and rel not in ALLOW_LOOPBACK and not rel.startswith(DEV_SCRIPTS):
            report(node.lineno, "loopback_literal", f"回环地址字面量 {value!r}：请用 core.constants.LOOPBACK")
        if not in_platform:
            for word, hint in PLATFORM_ONLY_STRINGS.items():
                if word in value:
                    report(node.lineno, "platform_only_string", f"字符串 {value!r} 含 {word!r}：{hint}")
    return problems


def scan() -> Dict[str, Any]:
    files = sorted(_iter_py_files())
    problems: List[Dict[str, Any]] = []
    for rel in files:
        problems.extend(check_file(rel))
    return {
        "ok": not problems,
        "scanned": len(files),
        "warnings": len(problems),
        "problems": problems,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    result = scan()
    if "--text" in sys.argv:
        print(f"扫描 {result['scanned']} 个 Python 文件，告警 {result['warnings']} 条")
        for item in result["problems"]:
            print(f"  {item['file']}:{item['line']}  [{item['rule']}]  {item['message']}")
        print("OK：业务代码没有绕过平台抽象层" if result["ok"] else "FAIL：存在绕过抽象层的写法")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
