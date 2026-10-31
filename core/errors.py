"""业务异常。API 层统一把它们翻译成结构化错误响应。"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ConsoleError(Exception):
    """控制台业务异常基类。

    code 用于前端做稳定判断（不要依赖中文文案）；hint 用于给出可操作的处置建议。
    """

    code = "console_error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        hint: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail or {}
        # 允许单次抛错覆盖类级别的 code / http_status
        if code:
            self.code = code
        if http_status:
            self.http_status = int(http_status)

    def to_dict(self) -> Dict[str, Any]:
        # FIX-T：与 main.py 里另外两个 handler 的响应结构对齐（都带 ok:false），
        # 前端只判断 payload.ok 时不会把业务错误当成成功。
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
        }


class SettingsError(ConsoleError):
    code = "settings_error"


class PathError(ConsoleError):
    code = "path_error"


class ConfigError(ConsoleError):
    code = "config_error"


class BackupError(ConsoleError):
    code = "backup_error"


class ServiceError(ConsoleError):
    code = "service_error"


class InstallError(ConsoleError):
    code = "install_error"


class CredentialError(ConsoleError):
    code = "credential_error"


class BridgeError(ConsoleError):
    code = "bridge_error"


class PreconditionError(ConsoleError):
    """前提条件不满足（例如桥接前服务未运行）。"""

    code = "precondition_failed"
    http_status = 409


class NotSupportedError(ConsoleError):
    """当前平台/形态不支持该操作（例如 Linux 占位实现）。"""

    code = "not_supported"
    http_status = 501
