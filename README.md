# AstrBot × SnowLuma 集成控制台（v0.1.1）

一个只在本机运行的单端口 Web 控制台，把 **AstrBot（机器人框架）** 与 **SnowLuma（OneBot v11 协议端）** 的安装、配置、启停、凭据和桥接连成一个流程，免除手动抄端口、抄 token 的苦活。

- 后端：Python 3.11+ / FastAPI / uvicorn（代码同时兼容 3.10，方便就地跑起来）
- 依赖的真实版本要求（实测，与需求文档的初稿有出入）：**AstrBot 需要 Python 3.12+**（PyPI 上 astrbot 4.29.3 声明 `requires_python >=3.12`），**SnowLuma lite 需要 Node 24.22.1+**（发布包 `package.json` 的 `engines.node`）。控制台的环境检测按这两个阈值判定，低于阈值会直接给出可执行的提示，而不是让你等到 pip / node 自己报错。
- 前端：Vue 3 + Vite + TypeScript，构建产物由 FastAPI 静态托管，**只有一个端口**
- 默认地址：<http://127.0.0.1:8765>
- 目标平台：Windows 10/11 x64；Linux 通过 LinuxAdapter 留好扩展位（未实现）

> **v0.1.1**：修掉七类真问题——uv 安装落点、Docker 桥接地址、首启超时与就绪判定、一次性密码抓取窗口、SnowLuma 历史目录静默无密码、进程树残留、控制台退出后子进程成孤儿。逐条对照见文末「v0.1.1 修了什么」。

## 快速开始

1. 双击 `start.bat`（首次会自动建 `.venv`、装依赖，并用清华源加速）。
2. 浏览器打开 <http://127.0.0.1:8765>。
3. 依次走：**环境检测 → 安装部署 → 服务管理 → 桥接向导**。

手动启动（等价于 `start.bat` 的最后一步）：

```bash
python -m pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
python main.py --host 127.0.0.1 --port 8765
```

> `web/dist` 是构建产物，未纳入版本库；首次运行请在 `web/` 下执行 `npm install && npm run build`（`start.bat` 检测到缺失时也会提示）。不装 Node 也能用后端 API，只是没有界面。

## 打包与安装（面向最终用户）

普通用户不用碰源码：`packaging\build\dist\Setup.exe` 就是带图形界面的安装包（约 19 MB，**自带 Python 运行时**，不需要预装 Python / Node）。

- **图形安装**：双击 `Setup.exe` → 选安装目录（默认 `%LOCALAPPDATA%\Programs\AstrBotConsole`）→ 勾选是否建快捷方式 → 安装完可直接启动。
- **静默安装**：`Setup.exe --silent --dir=D:\AstrBotConsole --no-launch`；日志在 `%TEMP%\astrbot-console-setup.log`。
- **绿色版**：直接用 `packaging\build\dist\AstrBotConsole\` 整个目录，数据落在 exe 同级的 `data\`，换机器拷走即可。
- **卸载**：运行安装目录里的 `uninstall.exe`。**先退出控制台再卸载**，否则 `AstrBotConsole.exe` 被占用会删不掉；默认保留 `data\`，加 `--purge` 才连用户数据一起删。

从源码重新打包（需要 `pip install pyinstaller`）：

```bash
python packaging/build.py            # 构建 web/dist + exe + Setup.exe
python packaging/build.py --skip-web # web/dist 已是最新时只重打 exe
```

产物为 `packaging/build/dist/AstrBotConsole/`（绿色版目录）与 `packaging/build/dist/Setup.exe`（安装包）。构建脚本会自动挑一个装了 PyInstaller 的解释器——PATH 里的 `python` 未必就是它。

> 双击启动时如果 8765 端口被占用，控制台会自动往后找一个可用端口并在窗口里打印实际地址，不会再一闪而过；启动失败时窗口也会停住等回车。

**完整的安装与使用说明见 [安装与使用教程.md](安装与使用教程.md)**：三种安装方式对比、界面导览、首次使用七步、数据目录与备份、卸载、命令行参数、常见问题 Q1–Q10。

## 三种部署形态

| 服务 | 路线 | 控制台实际动作 |
| --- | --- | --- |
| AstrBot | uv / pip 原生 | 建虚拟环境 → 装 `astrbot` → `astrbot init -y` 初始化数据目录 → `subprocess` 拉起 `astrbot run` |
| AstrBot | docker | 生成 compose 文件 → `docker compose up -d` / `down` / `docker logs -f astrbot` |
| SnowLuma | 原生 | 解压发布包到 `apps/SnowLuma` → 注入 `SNOWLUMA_ACCEPT_EULA`、`SNOWLUMA_ACCEPT_PRIVACY` → 用 `node index.mjs` 拉起（不跑 `launcher.bat`，它末尾有 `pause`，接进子进程会卡住） |

`deploy_mode` 只存在 `state/settings.json` 一处，服务管理、配置读写、凭据重置都读同一份；切换路线不会改动 `instances/`。

## 目录结构

```
kzt/
├─ api/                # FastAPI 路由（meta / env / install / service / config / credential / bridge / settings）
├─ core/
│  ├─ platform/        # 平台抽象层：WindowsAdapter 实现，LinuxAdapter 占位
│  ├─ detector/        # 环境检测（按路线动态显示，单项独立超时）
│  ├─ installer/       # AstrBot(uv/docker) 与 SnowLuma 安装器
│  ├─ config/          # 配置读写 + 改前备份 + 原子写
│  ├─ service/         # ServiceController：ProcessController / DockerComposeController
│  ├─ credential/      # 凭据库、EULA 记录、AstrBot 密码重置
│  ├─ bridge/          # 反向 WS 一键对齐与连通性校验
│  └─ loghub/          # 日志环形缓冲 + SSE 广播
├─ web/                # Vue 3 前端（src/ 源码，dist/ 构建产物）
├─ start.bat
└─ main.py
```

运行时数据默认就落在项目目录（`main.py` 的上一级，即 `D:\kzt`）；想放到别处，设环境变量 `ASTRBOT_CONSOLE_ROOT` 或改引导配置 `%LOCALAPPDATA%\AstrBotConsole\bootstrap.json` 的 `root_dir`：

```
<root>/
├─ apps/         # 可删可重装的服务程序
├─ instances/    # 永不覆盖的用户数据（迁移只拷这一层）
├─ downloads/    # 安装包缓存
├─ backups/      # 配置备份：<服务>_<文件名>_<时间戳>.json
├─ logs/         # 服务日志落盘
└─ state/        # settings.json / credentials.json / eula_acceptance.json / runtime.json
```

## 关键行为

- **环境检测**：Docker 专属项与 uv 专属项按当前路线动态显示，未选路线显示「待选择」。每项独立超时（6–20 秒），任何一项卡住都不拖垮整次检测；结果缓存 20 秒，重复打开页面不重复打网络。
- **协议确认**：SnowLuma 安装必须同时勾选 EULA 与隐私政策，按钮才解锁；确认记录写入 `state/eula_acceptance.json`（含时间戳、版本号、勾选项），可追溯。另提供「无法勾选？走人工确认」的退路。
- **配置写入**：每次改前自动备份到 `backups/`；同一秒内多次保存会追加 `-2`、`-3` 后缀，不会互相覆盖；写入是原子的，改完提示「需要重启」。
- **凭据库**：`state/credentials.json` 明文保存但权限收紧到仅当前用户（icacls）。SnowLuma 的 HTTP(3000) 与 WS(3001) 是两套 server，accessToken 分开存、分开填。
- **AstrBot 密码重置**：不能用 `astrbot password`——它是 `click.prompt(hide_input=True)`，在无 TTY 的子进程里会直接卡死。实际走非交互的 `astrbot conf set dashboard.password <pwd>`（顺带 `dashboard.username`），Docker 路线包一层 `docker exec astrbot ...`，改完重启服务。控制台生成的密码满足面板策略（≥8 位、含大小写与数字）。SnowLuma 侧不提供控制台重置（接口返回 409）。
- **桥接目标地址由 controller 决定**：`ServiceController.get_bridge_endpoint()` 返回建议地址，业务代码里没有任何 `127.0.0.1` 之外的写死——uv 路线给 `ws://127.0.0.1:6199/ws`；Docker 默认给 `ws://host.docker.internal:6199/ws`，并在容器内自动探测 host.docker.internal → 宿主机局域网 IP，取第一个可达；全不可达就阻断一键桥接，引导去「设置 → 高级」切退路。Compose 里带 `extra_hosts: host.docker.internal:host-gateway`，退路 `network_mode: host` 可一键切换并重新生成 compose。
- **启动就绪判定 = TCP + HTTP 双层**：先等端口可连，再 GET 到 2xx/3xx 才算就绪；同时并行监听进程退出，就绪前退出立刻失败并回传退出码与最后 200 行日志。超时全部可配（依赖安装 600s、首启就绪 180s、优雅停止 10s、Docker CLI 60s、compose up 300s、密码抓取窗口 180s）。
- **凭据抓取在启动之前就开始**：日志采集写内存环形缓冲（最近 2000 行）并落盘，抓取扫缓冲而不是只扫"新行"，所以一次性密码不会因为"读得太晚"而丢。凭据带 `password_status` 三态（`captured` / `unknown` / `user_managed`），未知时不渲染 `?token=`，并常驻三个补救入口：重新抓取 / 手动粘贴入库 / 走重置。
- **进程树与孤儿**：Windows 用 `CREATE_NEW_PROCESS_GROUP` 拉起，停止走 CTRL_BREAK → 10s → `taskkill /T /F`；主 pid 写 `state/pids.json`（原子写）。控制台退出（FastAPI shutdown + atexit 双注册）会按 pids.json 优雅停掉子进程；如果控制台被强杀，下次启动会自检 pids.json，在「服务」页提示"接管 / 结束"，不会静默拉起第二个实例。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/meta` | 版本、根目录、平台、端口 |
| GET | `/api/env/detect` | 环境检测（`?force=true` 跳过缓存） |
| POST | `/api/env/install` | 安装检测项（如 uv） |
| POST | `/api/install/astrbot` | 安装/更新 AstrBot（`mode=uv` 或 `docker`） |
| POST | `/api/install/astrbot/switch-mode` | 切换部署路线 |
| POST | `/api/install/snowluma` | 安装 SnowLuma（需双协议确认） |
| GET | `/api/install/jobs`、`/api/install/jobs/{id}` | 安装任务进度 |
| GET | `/api/service/status` | 两个服务的状态汇总 |
| POST | `/api/service/{name}/start` `/stop` `/restart` | 启停与重启 |
| GET | `/api/service/{name}/log` | SSE 实时日志（`?tail=` 补历史） |
| GET | `/api/service/orphans`、`/scan`、`/{name}/resolve` | 上次残留的孤儿进程：查询 / 重扫 / 接管或结束 |
| POST | `/api/service/{name}/kill-residual` | 端口未释放时清理残留 pid |
| GET/PUT/PATCH | `/api/config/{service}/{file}` | 读配置 / 写配置（自动备份） |
| GET/POST | `/api/config/{service}/backups/list`、`/api/config/backups/restore` | 备份列表与还原 |
| GET/PUT | `/api/credential/{service}` | 凭据读写 |
| POST | `/api/credential/{service}/reset` | AstrBot 面板密码重置 |
| POST | `/api/credential/{service}/recapture`、`/manual` | 重新抓取一次性密码 / 手动粘贴入库 |
| GET/POST | `/api/bridge/status`、`/api/bridge/apply`、`/api/bridge/verify` | 桥接对齐与校验 |
| GET | `/api/bridge/endpoint` | 按部署形态给出 SnowLuma 应连的目标（`?refresh=true` 容器内探测） |
| POST | `/api/bridge/fallback` | 一键切 `network_mode: host` 退路并重生成 compose |
| GET | `/api/config/snowluma/history` | SnowLuma 历史数据目录检测 |
| POST | `/api/config/snowluma/archive-config` | 把旧 config 归档到 backups，得到全新数据目录 |
| POST | `/api/settings/network-mode` | 切换 Docker 网络模式（bridge / host） |
| GET/PUT | `/api/settings` | 端口、镜像、根目录等设置 |
| GET/POST/DELETE | `/api/settings/eula` | 协议接受记录 |

## 平台扩展

业务代码里没有 `platform.system()`，也没有裸 `os.system`；所有平台操作都经 `core/platform/base.py` 的 `PlatformAdapter`。

新增 Linux 支持只需实现 `core/platform/linux.py` 的 `LinuxAdapter`（签名与 `WindowsAdapter` 完全一致），业务层无需改动。

## 常见问题

- **端口被占用**：环境检测会列出占用进程与 PID；改端口到「设置」，改完重启对应服务。
- **Docker 路线连不上 6199**：Windows 上 Docker Desktop 走 WSL2，容器内的 `127.0.0.1` 不是宿主机；桥接页会给出对应文案，需要用宿主机 IP 或端口映射。
- **SnowLuma 密码没抓到**：一次性密码只在首次启动时打印，重装数据目录才会重新生成；也可以到日志页翻 `SnowLuma` 频道。
- **没检测到 NTQQ**：SnowLuma 需要 NTQQ 客户端登录，装上并登录一次即可。
- **SnowLuma 安装包下载很慢或断掉**：GitHub Release 资产在国内经常只有几十 KB/s。两种办法：①「设置」里填 **GitHub 下载代理前缀**（如 `https://ghfast.top`，代理由你自行选择与信任），安装器会优先走代理、失败再回退直连；②手动下载 zip 放进 `downloads/` 目录（文件名需与 Release 资产一致，例如 `SnowLuma-v2.2.1-win-x64-lite.zip`），安装器检测到大于 1 MB 的同名文件会直接复用，跳过下载。
- **下载中途断了怎么办**：安装器把内容写到 `.part` 临时文件，重试时用 HTTP Range 从断点续传（默认最多 4 次尝试，20 秒无数据即判定卡死并重连）。

## 实测验证记录

以下都是在 Windows 11 + Python 3.12.10 + Node 24.22.1 上真机跑通的，不是纸面设计：

- **AstrBot uv 全链路**：建 venv → 清华源装 astrbot 4.29.3（约 160 个包，6 分 16 秒）→ `astrbot init -y` → 启动 → `http://127.0.0.1:6185/` 返回 200 → 重置密码 → `POST /api/auth/login` 拿到 JWT。
- **SnowLuma**：下载 v2.2.1 lite（6.5 MB）→ 解压 70 个文件 → 双协议注入 → 启动监听 5099 → `POST /api/login` 用托管密码登录成功；`http://127.0.0.1:5099/?token=<密码>` 免密链接有效（前端把该参数当密码去登录）。
- **配置读写**：连续改 3 次生成 3 个备份（同秒自动加 `-2`/`-3`），还原后值正确、BOM 形态保持不变。
- **桥接**：AstrBot 侧 `aiocqhttp` 开 6199 + token，SnowLuma 侧 `wsClients[0].url = ws://127.0.0.1:6199/ws`；重启 AstrBot 后 6199 处于 LISTENING，`/api/bridge/verify` 返回 `ok: true`。
- **持久化**：只重启控制台进程，两侧凭据、`deploy_mode`、桥接 token 全部还在。
- **路线切换**：uv → docker（本机无 Docker，检测项给出 missing）→ 切回 uv，`instances/` 不动、不用重装。
- **回归脚本**：`python scripts/e2e_api.py` 43 项全过；`python scripts/acceptance_check.py` 返回 `ok: true`（无禁止写法、40 条路由齐全、deploy_mode 单一来源、web/dist 存在）；`python scripts/check_abstraction.py` 扫描 52 个 Python 文件、0 告警。

## v0.1.1 修了什么

需求方给的增量文档里 FIX-01 ~ FIX-07，逐条落地：

| 编号 | 问题 | 修法 |
| --- | --- | --- |
| FIX-01 | `uv tool install` 落点不可控，装到全局去了 | 改成 `uv venv instances\AstrBot\venv --python 3.12` + `uv pip install --python <venv python> astrbot`；可执行文件统一走 `PlatformAdapter.venv_executable()`，不硬编码 `Scripts`；装完回读 `import astrbot` 的版本写进 settings；`init` 与 `run` 的 cwd 统一，init 后校验数据目录落在 `instances\AstrBot\data` |
| FIX-02 | Docker 路线桥接地址写死 `127.0.0.1` | 目标地址由 `ServiceController.get_bridge_endpoint()` 给；容器内自动探测 host.docker.internal / 局域网 IP；全不可达则阻断并引导切退路；compose 加 `extra_hosts`，可切 `network_mode: host` |
| FIX-03 | 首启无超时、判定不准 | TCP + HTTP(2xx/3xx) 双层就绪判定；分级超时全部可配；并行监听进程退出，就绪前退出立即失败并附退出码与尾部日志；6185 被别的 HTTP 服务占用时报"端口已被占用且非 AstrBot" |
| FIX-04 | 一次性密码抓取窗口错位 | 启动前就建日志采集 + 环形缓冲；抓取扫缓冲、任务化窗口；UI 常驻"重新抓取 / 手动粘贴 / 走重置"；命中即原子写库并记 source 与 captured_at |
| FIX-05 | SnowLuma 历史目录静默无密码 | 安装/启动/重启前先检测 `config\runtime.json`、`onebot_*.json`，命中则 409 + 前置弹窗，只给"新建全新数据目录"或"我已自行重置"两条路；凭据加 `password_status` 三态，未知时不渲染 `?token=` |
| FIX-06 | 进程树杀不干净 | `CREATE_NEW_PROCESS_GROUP` + CTRL_BREAK → 10s → `taskkill /PID /T /F`（经 `PlatformAdapter.kill_tree`，业务代码不拼命令）；`state\pids.json` 记主 pid；停止后复核端口，未释放就提示残留 pid 并支持一键清理 |
| FIX-07 | 控制台退出后子进程成孤儿 | FastAPI shutdown + atexit 双注册回收；设置项 `shutdown_child_processes` 默认开；启动自检 pids.json，提示"接管 / 结束"，禁止重复拉起第二个实例 |

新增的两个自检脚本直接进 CI 心智：`scripts/check_abstraction.py`（扫平台抽象层越界，0 告警）与 `scripts/acceptance_check.py`（路由齐全性 + 禁止写法 + deploy_mode 单一来源 + web 产物）。

## 明确不做的事

Linux 实际实现、跨路线数据迁移、整体备份恢复、多实例、控制台鉴权、QQ 自动更新冻结。
