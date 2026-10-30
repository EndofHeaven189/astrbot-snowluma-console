"""日志采集与 SSE 推送。"""

from core.loghub.hub import LogHub, get_loghub

__all__ = ["LogHub", "get_loghub", "SERVICE_ALL"]
SERVICE_ALL = "__all__"
