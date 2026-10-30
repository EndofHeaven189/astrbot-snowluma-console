"""AstrBot x SnowLuma 集成控制台 —— 核心业务包。

分层约定（见需求文档 5.3）：
    core.platform   平台抽象层（Windows / Linux）
    core.detector   环境检测
    core.installer  安装器
    core.config     配置文件读写 + 备份 + 校验
    core.service    服务启停适配器（subprocess / docker）
    core.credential 凭据抓取与保管
    core.bridge     桥接向导
    core.loghub     日志采集与 SSE 推送
"""

from core.constants import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
