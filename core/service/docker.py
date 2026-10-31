"""Docker 容器控制器（AstrBot 路线 B）。

只调 docker CLI 并解析输出，不引入 Docker SDK（需求 5.4）。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Dict, List, Optional

from core.constants import DOCKER_HOST_ALIAS, LOOPBACK
from core.errors import ServiceError
from core.loghub import get_loghub
from core.platform import get_platform
from core.service.base import ServiceController, ServiceStatus
from core.utils import tail_lines

_FOLLOW_THREADS: Dict[str, threading.Event] = {}


class DockerComposeController(ServiceController):
    deploy_mode = "docker"

    def __init__(
        self,
        name: str,
        *,
        container: str,
        compose_file: str,
        port: Optional[int] = None,
        ready_url: Optional[str] = None,
        service_label: Optional[str] = None,
        bridge_port: Optional[int] = None,
    ) -> None:
        self.name = name
        self.container = container
        self.compose_file = compose_file
        self.service_label = service_label or container
        self._port = port
        self._bridge_port = bridge_port
        self._ready_url = ready_url

    # ---------------------------------------------------------------- 基础能力
    def _cli_timeout(self) -> float:
        from core.settings import get_settings

        return float(get_settings().timeout("docker_cli"))

    def _docker(self, args: List[str], timeout: float = 0.0):
        return get_platform().run_capture(["docker", *args], timeout=timeout or self._cli_timeout())

    def _compose(self, args: List[str], timeout: float = 0.0):
        from core.settings import get_settings

        timeout = timeout or float(get_settings().timeout("compose_up"))
        adapter = get_platform()
        code, out = adapter.run_capture(
            ["docker", "compose", "-f", self.compose_file, *args], timeout=timeout
        )
        if code != 0 and ("is not a docker command" in out or code == 127):
            code, out = adapter.run_capture(
                ["docker-compose", "-f", self.compose_file, *args], timeout=timeout
            )
        return code, out

    # ---------------------------------------------------------------- 状态
    def is_installed(self) -> bool:
        return os.path.exists(self.compose_file)

    def _container_state(self) -> Optional[str]:
        code, out = self._docker(
            ["inspect", "-f", "{{.State.Status}}", self.container], timeout=20
        )
        if code != 0:
            return None
        state = out.strip().splitlines()[0] if out.strip() else ""
        return state or None

    def _container_version(self) -> Optional[str]:
        code, out = self._docker(
            [
                "inspect",
                "-f",
                '{{index .Config.Labels "org.opencontainers.image.version"}}',
                self.container,
            ],
            timeout=20,
        )
        label = out.strip() if code == 0 else ""
        if label and label not in ("<no value>", "None"):
            return label
        code, out = self._docker(
            ["exec", self.container, "astrbot", "--version"], timeout=30
        )
        if code == 0 and out.strip():
            return out.strip().splitlines()[0][:40]
        code, out = self._docker(
            ["inspect", "-f", "{{.Config.Image}}", self.container], timeout=20
        )
        if code == 0 and out.strip():
            image = out.strip()
            return image.split(":")[-1] if ":" in image else image
        return None

    def status(self) -> ServiceStatus:
        installed = self.is_installed()
        state = self._container_state()
        running = state == "running"
        version = self._container_version() if state else None
        pid = None
        if running:
            code, out = self._docker(["inspect", "-f", "{{.State.Pid}}", self.container], timeout=20)
            if code == 0 and out.strip().isdigit():
                pid = int(out.strip())
        return ServiceStatus(
            name=self.name,
            installed=installed,
            running=running,
            version=version,
            deploy_mode=self.deploy_mode,
            pid=pid,
            port=self._port,
            url=self._ready_url,
            detail={"container": self.container, "state": state, "compose": self.compose_file},
        )

    # ---------------------------------------------------------------- 生命周期
    async def start(self, wait: bool = False, timeout: float = 300.0) -> ServiceStatus:
        if not self.is_installed():
            raise ServiceError(
                "compose 编排文件不存在",
                hint="请先完成 AstrBot Docker 路线安装。",
                detail={"compose": self.compose_file},
            )
        hub = get_loghub()
        hub.append(self.name, "=== docker compose up -d ===")
        from core.settings import get_settings

        code, out = await asyncio.to_thread(
            self._compose, ["up", "-d"], float(get_settings().timeout("compose_up"))
        )
        for line in out.splitlines():
            hub.append(self.name, line)
        if code != 0:
            raise ServiceError(
                "docker compose up 失败",
                hint="检查 Docker Desktop 是否运行、镜像是否可拉取（可在设置里开启国内镜像）。",
                detail={"output": out[-4000:]},
            )
        self._start_follow()
        if wait:
            await self.wait_ready(timeout=timeout)
        return await asyncio.to_thread(self.status)

    async def stop(self, force: bool = False) -> ServiceStatus:
        hub = get_loghub()
        hub.append(self.name, "=== docker compose down ===")
        self._stop_follow()
        from core.settings import get_settings

        code, out = await asyncio.to_thread(
            self._compose, ["down"], max(60.0, float(get_settings().timeout("compose_up")) / 2)
        )
        for line in out.splitlines():
            hub.append(self.name, line)
        if code != 0:
            raise ServiceError(
                "docker compose down 失败",
                hint="可在「服务」页强制执行 docker rm -f astrbot。",
                detail={"output": out[-4000:]},
            )
        return await asyncio.to_thread(self.status)

    # ---------------------------------------------------------------- 日志
    def _start_follow(self) -> None:
        if self.name in _FOLLOW_THREADS:
            return
        stop_event = threading.Event()
        _FOLLOW_THREADS[self.name] = stop_event
        hub = get_loghub()
        adapter = get_platform()

        def _pump() -> None:
            try:
                handle = adapter.spawn(
                    ["docker", "logs", "-f", "--tail", "200", self.container],
                    capture=True,
                    name=f"docker-logs-{self.container}",
                )
                popen = handle.popen
                if popen is None or popen.stdout is None:
                    return
                for line in iter(popen.stdout.readline, ""):
                    if stop_event.is_set():
                        break
                    if not line:
                        break
                    hub.append(self.name, line)
                adapter.kill(handle, force=True)
            except OSError:
                pass
            finally:
                _FOLLOW_THREADS.pop(self.name, None)

        threading.Thread(target=_pump, name=f"dockerlogs-{self.name}", daemon=True).start()

    def _stop_follow(self) -> None:
        event = _FOLLOW_THREADS.pop(self.name, None)
        if event:
            event.set()

    def read_log(self, tail: int = 400) -> List[str]:
        code, out = self._docker(["logs", "--tail", str(tail), self.container], timeout=45)
        if code == 0 and out.strip():
            return out.splitlines()
        hub = get_loghub()
        buffered = hub.snapshot(self.name)
        if buffered:
            return buffered[-tail:]
        path = hub.file_path(self.name)
        return tail_lines(path, tail) if path else []

    # ---------------------------------------------------------------- 桥接端点
    def _network_mode(self) -> str:
        try:
            from core.settings import get_settings

            mode = str(get_settings().get("astrbot.docker_network_mode", "bridge") or "bridge")
        except Exception:
            mode = "bridge"
        return mode if mode in ("bridge", "host") else "bridge"

    def get_bridge_endpoint(self, dial_from: str = "container") -> Dict[str, Any]:
        """容器该用哪个地址回连宿主机的 OneBot 反向 WS（FIX-02）。

        目标地址只能由控制器给出，业务层不许写死 IP。按优先级给候选：
        1. host.docker.internal（Docker Desktop 默认，靠 compose 的 extra_hosts: host-gateway 兜底）
        2. 宿主机局域网 IP（host.docker.internal 不可用时的退路 1）
        3. 127.0.0.1（仅在 network_mode: host 的退路 2 下有效）
        """
        port = int(self._bridge_port or self._port or 6199)
        mode = self._network_mode()
        adapter = get_platform()
        lan_ip = adapter.local_ip()
        candidates: List[Dict[str, Any]] = []
        if mode == "host":
            candidates.append(
                {
                    "strategy": "host-network",
                    "host": LOOPBACK,
                    "port": port,
                    "url": f"ws://{LOOPBACK}:{port}/ws",
                    "label": "容器与宿主机共享网络（network_mode: host）",
                }
            )
        else:
            candidates.append(
                {
                    "strategy": "host-gateway",
                    "host": DOCKER_HOST_ALIAS,
                    "port": port,
                    "url": f"ws://{DOCKER_HOST_ALIAS}:{port}/ws",
                    "label": "Docker Desktop 网关别名（extra_hosts: host-gateway）",
                }
            )
        if lan_ip and lan_ip != LOOPBACK:
            candidates.append(
                {
                    "strategy": "lan-ip",
                    "host": lan_ip,
                    "port": port,
                    "url": f"ws://{lan_ip}:{port}/ws",
                    "label": "宿主机局域网 IP（退路 1）",
                }
            )
        if mode != "host":
            candidates.append(
                {
                    "strategy": "host-network",
                    "host": LOOPBACK,
                    "port": port,
                    "url": f"ws://{LOOPBACK}:{port}/ws",
                    "label": "服务器模式退路：需在「设置 → 高级」切到 network_mode: host 后重新生成 compose",
                }
            )
        first = candidates[0]
        return {
            "route": self.deploy_mode,
            "dial_from": dial_from,
            # 契约：未探测前 reachable 为 None，探测后由 resolve_bridge_endpoint 覆盖
            "reachable": None,
            "reason": None,
            "strategy": first["strategy"],
            "host": first["host"],
            "port": port,
            "url": first["url"],
            "network_mode": mode,
            "candidates": candidates,
            "note": "Docker 路线：容器内的 127.0.0.1 是容器自己，必须用网关别名或宿主机 IP。",
        }

    def probe_from_container(self, host: str, port: int, timeout: float = 5.0) -> bool:
        """在容器内部探测某个地址能否连通（一键桥接前自动跑）。"""
        script = (
            "import socket,sys;"
            "s=socket.socket();s.settimeout(%d);"
            "sys.exit(0 if s.connect_ex((%r,%d))==0 else 1)"
        ) % (int(timeout), host, int(port))
        code, _ = self._docker(["exec", self.container, "python", "-c", script], timeout + 15)
        return code == 0

    def resolve_bridge_endpoint(self) -> Dict[str, Any]:
        """逐个探测候选地址，取第一个容器内可达者。

        全不可达时返回 None 式结果，由调用方阻塞并引导用户去「设置 → 高级」切退路。
        """
        info = self.get_bridge_endpoint("container")
        if self._container_state() != "running":
            info["reachable"] = False
            info["reason"] = "container_not_running"
            return info
        for candidate in info["candidates"]:
            if self.probe_from_container(candidate["host"], candidate["port"]):
                info.update(
                    {
                        "reachable": True,
                        "strategy": candidate["strategy"],
                        "host": candidate["host"],
                        "url": candidate["url"],
                        "reason": "",
                    }
                )
                return info
        info["reachable"] = False
        info["reason"] = "unreachable"
        info["note"] = (
            "容器连不上宿主机的 OneBot 端口：请确认 AstrBot 已启动并通过 6199 端口监听，"
            "或在「设置 → 高级」把容器网络切换为 network_mode: host 后重新生成 compose。"
        )
        return info

    # ---------------------------------------------------------------- 就绪
    def ready_url(self) -> Optional[str]:
        return self._ready_url

    async def wait_ready(self, timeout: float = 0.0) -> bool:
        """就绪判定（FIX-03 双层）：容器在跑 → TCP 可连 → HTTP 2xx/3xx。"""
        from core.settings import get_settings

        if not timeout or timeout <= 0:
            timeout = float(get_settings().timeout("service_ready"))
        adapter = get_platform()
        host, port = LOOPBACK, self._port or 6185
        ready_path = "/"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._container_state() != "running":
                await asyncio.sleep(1.0)
                continue
            if not await asyncio.to_thread(adapter.tcp_ready, host, port, 2.0):
                await asyncio.sleep(1.0)
                continue
            ok, status = await asyncio.to_thread(
                adapter.http_ready, f"http://{host}:{port}{ready_path}", 3.0
            )
            if ok:
                return True
            await asyncio.sleep(1.5)
        return False
