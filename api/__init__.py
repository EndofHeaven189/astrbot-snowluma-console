"""HTTP 接口层（需求第 7 章）。

约定：
* 所有接口前缀 /api
* 业务异常统一由 core.errors.ConsoleError 抛出，main.py 里的处理器转成 JSON
* 这一层只做参数校验与编排，不直接调用 subprocess / 平台 API
"""

from __future__ import annotations
