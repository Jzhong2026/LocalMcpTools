# LocalMcpTools — 实现计划

> 状态: **v0.1 草案**
> 最后更新: 2026-08-07
> 配套文档: [requirements.md](./requirements.md)

---

## 0. 技术栈总览

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | **Python 3.11+** | MCP 官方 SDK 主推, 生态最熟 |
| MCP 框架 | **官方 MCP Python SDK** | 使用其 FastMCP/ASGI 能力；阶段 0 用目标客户端验证后锁版本 |
| MCP 传输 | **stdio 默认；Streamable HTTP 可选** | 最小权限客户端优先 stdio；共享模式才启 HTTP |
| HTTP 框架 | **FastAPI** + **uvicorn**（共享模式） | 承载控制面与可选的 Streamable HTTP MCP endpoint |
| 数据库 | **SQLite**(内置 `sqlite3`) | 单机零运维, 够用 |
| 异步 | **asyncio**(Python 内置) | MCP SDK 异步友好 |
| 编码探测 | **chardet** | 成熟稳定 |
| UI 自动化 | **uiautomation** | 对 UI Automation 支持细 |
| 进程信息 | **psutil** | Windows 子进程树查找方便 |
| 前端 | **Angular 19**(用户指定) | 与 TypeScript 后端接口清晰 |
| UI 组件库 | **Angular Material** | 表格 / 表单 / 状态指示器齐全 |
| 打包 | 暂不上 PyInstaller, 先 `pip install -e .` + `python -m localmcptools` | 调试期方便, 后期再考虑打包 |

---

## 1. 仓库结构

```text
LocalMcpTools/
├── README.md                          # 项目简介 + 启动方式
├── pyproject.toml                     # Python 包配置 + 依赖
├── requirements.txt                   # 运行时依赖(锁定版本)
├── requirements-dev.txt               # 开发依赖(test / lint)
├── .gitignore
├── .editorconfig
├── docs/
│   ├── requirements.md                # 需求文档
│   ├── implementation-plan.md         # 本文件
│   ├── tool-reference.md              # 每个 tool 的接口签名(后续补)
│   └── troubleshooting.md             # 常见问题
├── src/
│   └── localmcptools/
│       ├── __init__.py
│       ├── __main__.py                # python -m localmcptools 入口
│       ├── cli.py                     # argparse: start / stop / status / install
│       ├── server.py                  # FastAPI + FastMCP 装配
│       ├── control_api.py             # UI 用的控制面路由
│       ├── policy/                    # profile、授权、审批与落盘前脱敏
│       ├── workspaces/                # workspace registry、项目探测与预设
│       ├── config/
│       │   ├── __init__.py
│       │   ├── paths.py               # 各种路径解析(%APPDATA%\...)
│       │   ├── settings.py            # 读取/写入 config.json
│       │   └── defaults.py            # 内置默认值
│       ├── persistence/
│       │   ├── __init__.py
│       │   ├── db.py                  # SQLite 连接 + 迁移
│       │   ├── audit.py               # audit log 读写
│       │   └── log_files.py           # 原始输出文件管理
│       ├── safety/
│       │   ├── __init__.py
│       │   ├── rules.py               # 规则加载 / 匹配
│       │   └── builtin/               # 内置规则(JSON)
│       │       ├── format-volume.json
│       │       ├── disk-wipe.json
│       │       ├── rm-system.json
│       │       ├── registry.json
│       │       ├── boot-loader.json
│       │       ├── firewall-reset.json
│       │       ├── privilege-escalation.json
│       │       ├── kill-protected.json
│       │       └── remote-download-exec.json
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── _common.py             # envelope / 错误格式
│       │   ├── workspace.py            # inspect / search_text / run_test / build / git_status
│       │   ├── diagnostics.py          # collect / explain_failure
│       │   ├── output.py               # artifact 的 tail / range / search
│       │   ├── environment.py         # environment.get
│       │   ├── shell.py               # shell.run_command / start_background / get_status / stop / list_backgrounds / tail_log
│       │   ├── process.py             # process.list_processes / list_listening_ports / find_by_port / kill
│       │   ├── fs.py                  # fs.read_range / tail_log_file / grep_files
│       │   ├── ui.py                  # ui.screenshot_* / get_ui_tree / find_element / click_element / type_text
│       │   ├── vscode.py              # vscode.get_problems / get_installed_extensions / get_logs / get_debug_sessions
│       │   └── runtime.py             # runtime.detect_runtime / get_env / list_path
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── runner.py              # 进程执行核心(超时、编码、tee)
│       │   ├── encoding.py            # 编码探测 + 解码
│       │   ├── concurrency.py         # Semaphore + 队列可见性
│       │   └── background.py          # 后台进程生命周期管理
│       └── ui_assets/                 # Angular 构建产物(由 build_frontend.bat 注入)
├── ui/                                # Angular 前端源码
│   ├── angular.json
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── index.html
│   │   ├── main.ts
│   │   ├── app/
│   │   │   ├── app.config.ts
│   │   │   ├── app.routes.ts
│   │   │   ├── app.component.ts
│   │   │   ├── core/
│   │   │   │   ├── api.service.ts
│   │   │   │   └── models.ts
│   │   │   ├── features/
│   │   │   │   ├── dashboard/
│   │   │   │   ├── audit/
│   │   │   │   ├── settings/
│   │   │   │   ├── rules/
│   │   │   │   └── mcp-config/
│   │   │   └── shared/
│   │   └── styles.css
│   └── README.md
├── tests/
│   ├── unit/
│   │   ├── test_encoding.py
│   │   ├── test_rules.py
│   │   ├── test_audit.py
│   │   └── test_runner.py
│   └── integration/
│       ├── test_mcp_discovery.py
│       └── test_end_to_end.py
└── scripts/
    ├── dev_backend.bat                # 启 server(开发模式)
    ├── dev_frontend.bat               # 启 Angular dev server
    ├── build_frontend.bat             # 构建 Angular, 产物注入 src/localmcptools/ui_assets/
    ├── install_windows_task.ps1       # 创建开机自启计划任务
    └── uninstall_windows_task.ps1
```

---

## 2. MCP 代码结构细节

### 2.1 FastMCP 集成方式

```python
# src/localmcptools/server.py (实际 import/API 以阶段 0 锁定的官方 SDK 为准)
from mcp.server.fastmcp import FastMCP
from fastapi.staticfiles import StaticFiles

mcp = FastMCP("LocalMcpTools")

# 注册 tools
from .tools import environment, shell, process, fs, ui, vscode, runtime

app = create_fastapi_app(title="LocalMcpTools")

# 仅在共享模式装配 SDK 提供的 Streamable HTTP ASGI app；
# stdio 模式不启动 HTTP MCP endpoint。
app.mount("/mcp", mcp.streamable_http_app())

# UI 路由(Angular 构建产物)
app.mount("/ui", StaticFiles(directory="ui_assets", html=True), name="ui")

# 控制面 API
from .control_api import router as control_router
app.include_router(control_router, prefix="/api")
```

**端口策略**:
- stdio 模式无监听端口；共享 HTTP 模式启动时读 `server.json`
- 有 HTTP 时优先用配置端口；端口为 `0` 才由 OS 分配并写回。UI、状态和 MCP snippet 都读取同一实际端口
- 监听 `127.0.0.1`, 不绑 `0.0.0.0`
- 共享 HTTP 验证 `Origin` / `Host`，并使用本机保存的 bearer secret；UI 控制面额外防 CSRF
- UI 首页 `/` 跳 `/ui/`
- 端口冲突 → 递增重试 3 次 → 失败报错

### 2.2 Tool 分组与命名空间

| Namespace | 工具 |
|---|---|
| `environment` | `environment.get` |
| `workspace` | `workspace.inspect`, `workspace.search_text`, `workspace.run_test`, `workspace.build`, `workspace.git_status` |
| `diagnostics` | `diagnostics.collect`, `diagnostics.explain_failure` |
| `output` | `output.tail`, `output.read_range`, `output.search` |
| `shell` | `shell.run_command`（仅 `workspace_exec` profile；不作为默认工作流） |
| `process` | `process.start_dev_server`, `process.get_status`, `process.stop_managed`, `process.list_managed`, `process.list_listening_ports` |
| `fs` | `fs.read_range`, `fs.tail_log_file`, `fs.grep_files`（均限已注册 workspace/artifact） |
| `ui` | `ui.get_ui_tree`, `ui.find_element`, `ui.click_element`, `ui.type_text`（默认关闭，限已授权窗口） |
| `vscode` | `vscode.get_problems`, `vscode.get_installed_extensions`, `vscode.get_logs`, `vscode.get_debug_sessions` |
| `runtime` | `runtime.detect_runtime`, `runtime.get_env`, `runtime.list_path` |

**namespace 的好处**: agent 看到 tool 列表时分组清楚; UI 也按 namespace 折叠。

**Profile 与授权**:

| Profile | 默认能力 | 禁止事项 |
|---|---|---|
| `observe` | 环境/项目诊断、受限搜索、读取受保护 artifact、端口只读 | 写文件、任意 shell、安装依赖、进程终止、UI 输入 |
| `workspace_exec` | 已注册 workspace 内的预设 test/build/lint 与受控 shell | workspace 外路径、提权、未批准网络下载执行 |
| `managed_process` | 启停本 server 创建并登记的 Job Object 进程 | 任意 PID kill 或继承历史孤儿进程 |
| `interactive_ui` | 指定窗口的 UI 树、查找与经批准的输入 | 任意桌面、凭据窗口、系统设置 |

请求中的 `cwd`、`agent`、`allow_dangerous` 只用于审计/提示。授权由服务端以 `client_instance + profile + workspace + policy_version + approval` 判定。

### 2.3 统一返回 envelope

```python
# src/localmcptools/tools/_common.py
from typing import Any
from pydantic import BaseModel

class ToolMeta(BaseModel):
    tool: str
    duration_ms: int
    audit_id: str
    log_path: str | None = None
    run_id: str
    output_handle: str | None = None
    next_actions: list[str] = []

class ToolError(BaseModel):
    code: str
    message: str
    suggestion: str | None = None
    blocked_by: str | None = None
    severity: str | None = None
    approval_id: str | None = None

class ToolResponse(BaseModel):
    ok: bool
    data: Any | None = None
    meta: ToolMeta
    error: ToolError | None = None
```

**约定**:
- 所有 tool 装饰器返回 `ToolResponse` 实例
- `meta.log_path` 永远给, 即使成功也方便查
- `error.blocked_by` 在被拦截时有值, 包含规则 id
- `error.severity` 在被拦截时有值
- 副作用操作在未经授权时返回 `approval_required`，而不是让 agent 重试或传入绕过参数
- 大输出只返回摘要；通过受权限检查的 `output_handle` 分页读取

---

## 3. 数据存储详细设计

### 3.1 目录结构

```text
%APPDATA%\LocalMcpTools\
├── config.json               # 主配置(用户可手编)
├── server.json               # 当前端口 + pid + 启动时间
├── audit.sqlite              # audit log 元数据
├── audit.sqlite-wal          # WAL 文件(SQLite 自动)
├── logs\                     # 原始 stdout / stderr 文件
│   └── 2026-08-07\
│       ├── bg-bg001-12345.log
│       └── cmd-cmd002-67890.log
├── rules.d\
│   ├── builtin\              # 跟包一起发, 默认不动
│   │   └── *.json
│   └── custom\               # 用户自定义
│       └── *.json
├── cache\                    # 临时缓存(如 UI 树快照)
└── artifacts\                # 受控输出；不能由调用方指定任意路径
```

### 3.2 config.json schema

```jsonc
{
  "version": 1,
  "server": {
    "host": "127.0.0.1",
    "port": 7890,
    "log_level": "INFO"
  },
  "security": {
    "transport_mode": "stdio",
    "http_shared_mode_enabled": false,
    "origin_allowlist": ["http://127.0.0.1"],
    "redact_before_persist": true
  },
  "workspaces": {
    "registered_roots": [],
    "default_profile": "observe"
  },
  "shell": {
    "default_timeout_ms": 120000,
    "max_timeout_ms": 3600000,
    "max_concurrent": 4,
    "queue_timeout_ms": 600000,
    "tool_overrides": {
      "git_clone": 600000,
      "npm_install": 1800000
    },
    "default_encoding": "auto",
    "powershell_args": [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"
    ]
  },
  "audit": {
    "retention_days": 30,
    "max_total_size_mb": 2048,
    "cleanup_interval_hours": 6
  },
  "ui": {
    "auto_open_browser": true,
    "theme": "system"
  },
  "windows_task": {
    "enabled": false,
    "task_name": "LocalMcpTools",
    "run_as_user": false
  }
}
```

### 3.3 SQLite schema

```sql
-- audit.sqlite

CREATE TABLE IF NOT EXISTS calls (
    id              TEXT PRIMARY KEY,
    timestamp       INTEGER NOT NULL,
    agent           TEXT,
    tool            TEXT NOT NULL,
    client_instance TEXT,
    workspace_id    TEXT,
    profile         TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    approval_id     TEXT,
    run_id          TEXT NOT NULL,
    args_redacted   TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    error_code      TEXT,
    error_message   TEXT,
    blocked_by      TEXT,
    severity        TEXT,
    exit_code       INTEGER,
    stdout_bytes    INTEGER,
    stderr_bytes    INTEGER,
    duration_ms     INTEGER NOT NULL,
    log_path        TEXT,
    status          TEXT NOT NULL,
    pid             INTEGER,
    finished_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_calls_tool ON calls(tool);
CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent);
CREATE INDEX IF NOT EXISTS idx_calls_ok ON calls(ok);

CREATE TABLE IF NOT EXISTS background_processes (
    id              TEXT PRIMARY KEY,
    command         TEXT NOT NULL,
    cwd             TEXT,
    pid             INTEGER NOT NULL,
    log_path        TEXT NOT NULL,
    started_at      INTEGER NOT NULL,
    persistent      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    exit_code       INTEGER,
    finished_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_bg_status ON background_processes(status);

CREATE TABLE IF NOT EXISTS rule_hit_stats (
    rule_id         TEXT PRIMARY KEY,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    last_hit_at     INTEGER,
    last_hit_cmd    TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    requested_capability TEXT NOT NULL,
    action_digest   TEXT NOT NULL,
    status          TEXT NOT NULL,
    expires_at      INTEGER NOT NULL,
    approved_at     INTEGER,
    consumed_at     INTEGER
);
```

### 3.4 写入策略

- 每条 tool 调用在调用开始时 `INSERT` 一行 `status='queued'`, 结束时 `UPDATE` 状态
- stdout / stderr 原始内容**不写 DB**, 走受 ACL 保护的 artifact 文件；DB 只存 handle、路径和大小
- 参数、输出摘要和 artifact 在落盘前脱敏；secret/token 不写入 audit、URL 或 tool 结果
- WAL 模式, 高并发友好
- `audit.retention_days` 触发清理, 定时任务每 6h 跑一次
- `audit.max_total_size_mb` 触发时按最旧优先删除

---

## 4. 安全规则详细设计

### 4.1 规则文件格式

```jsonc
{
  "id": "block-format-volume",
  "description": "拒绝任何磁盘格式化操作",
  "severity": "critical",
  "default_action": "block",
  "allow_override": true,
  "match": {
    "type": "any_of",
    "rules": [
      { "cmd_name": "Format-Volume" },
      { "cmd_name": "format" },
      { "cmd_name": "diskpart" },
      { "cmd_name": "cipher",  "args_match": "/w" },
      { "regex": "dd\\s+if=.*of=/dev/(sd|nvme|hd)" }
    ]
  },
  "suggestion": "磁盘格式化操作请在 PowerShell 管理员模式下手动确认。"
}
```

### 4.2 授权与规则流程

```text
1. resolve_client_identity() -> client_instance
2. resolve_workspace(request.workspace_id) -> canonical root
3. evaluate_profile(client_instance, workspace, requested_capability)
4. validate path/cwd/env/command/preset against profile allowlist
5. require unexpired user approval for configured side effects
6. run deny rules as defense in depth (critical 永不由 agent 参数绕过)
7. create audit run and execute
```

命令黑名单并非主权限机制：编码命令、脚本文件、shell 转义和动态拼接都必须在测试中覆盖。`allow_dangerous` 不再是 tool 参数；需要升级能力时由用户在 UI/CLI 创建短时、一次性的 approval。

### 4.3 内置规则首批清单

| 规则 id | severity | 描述 |
|---|---|---|
| `block-format-volume` | critical | Format-Volume / format / diskpart |
| `block-disk-wipe` | critical | `cipher /w:` / `dd of=/dev/...` |
| `block-system-rm` | critical | 删除 `C:\Windows` / `C:\Program Files` / 盘根 |
| `block-registry-delete` | high | `Remove-Item HKLM:\*` / `reg delete HKLM` |
| `block-boot-loader` | critical | `bcdedit` / `bootrec` / `bcdboot` |
| `block-firewall-reset` | high | `netsh advfirewall reset` |
| `block-privilege-escalation` | high | `net localgroup administrators ... /add` |
| `block-kill-protected` | critical | 杀 `csrss` / `lsass` / `smss` / `wininit` |
| `block-remote-download-exec` | high | `IEX (New-Object Net.WebClient).DownloadString` |
| `block-rdp-enable` | medium | 启用远程桌面 |

---

## 5. 后台进程与生命周期

### 5.1 server 启动流程

```text
1. 读 server.json
2. 若端口被占用:
   - 若是同一进程的(检查 pid): 重连
   - 否则: 报错并退出
3. 若无 server.json 且为共享 HTTP 模式:
   - 选择配置端口或 OS 分配端口, 写 server.json
   - 启动 FastAPI + FastMCP
   - 记录 pid
4. 启动后台清理任务(audit retention)
5. 启动后台进程监控(每 30s 同步 background_processes 状态)
6. 若 ui.auto_open_browser: 调 os.startfile 打开 http://127.0.0.1:port/ui/
```

### 5.2 server 退出流程

```text
1. 收到 SIGTERM / Ctrl+C / install_stop
2. 把所有由本 server Job Object 管理且 persistent=0 的后台进程:
   - Windows: 关闭 Job Object / 终止受管进程树
   - 优雅停止(terminate) → 等 5s → kill
3. 关闭 SQLite 连接
4. 删除 server.json
5. 退出
```

### 5.3 开机自启动策略

**推荐方案: Windows 计划任务(用户级, 不需管理员)**

```powershell
# scripts/install_windows_task.ps1
$TaskName = "LocalMcpTools"
$Action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "-m localmcptools start" `
    -WorkingDirectory "D:\AI\Projects\LocalMcpTools"

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "LocalMcpTools — MCP server for local agents"
```

**`localmcptools start` 的行为**:
- 启动 server, 把 stdout / stderr 重定向到 `%APPDATA%\LocalMcpTools\server.log`
- 用 `pythonw.exe` 或 `subprocess.DETACHED_PROCESS` 让 server 独立于父 shell 运行
- 启动脚本立即退出

**失败模式**:
- 任务触发但 server 已经在跑 → 启动脚本检测 `server.json` 里的 pid 仍存活就跳过
- 启动失败 → 写 `server.log`, 不弹窗(避免登录时弹)

**为什么不注册成 Windows 服务**:
- 需要管理员权限
- 启动更早(系统层), 不适合"用户登录后才有 UI"的场景
- 计划任务已经够用, 复杂度低一个量级

**为什么不放 Startup 文件夹**:
- 没有失败重试
- 路径不能带环境变量

### 5.4 客户端 CLI

```text
localmcptools start        # 启动 server(后台)
localmcptools stop         # 停止 server
localmcptools restart      # 重启
localmcptools status       # 输出 pid / 端口 / 后台进程数 / 最近 5 条调用
localmcptools ui           # 浏览器打开 UI
localmcptools install      # 创建计划任务(开机自启)
localmcptools uninstall    # 删除计划任务
localmcptools logs         # tail -f server.log
```

---

## 6. 前端 (Angular 19) 详细设计

### 6.1 路由

```text
/ui/
├── /                              # 重定向到 /dashboard
├── /dashboard                     # 总览
│   ├── 当前 server 状态
│   ├── 后台进程列表
│   ├── 监听端口
│   └── 最近 5 条调用摘要
├── /audit                         # 调用记录
│   ├── 列表(分页 + 过滤)
│   ├── 详情抽屉(命令 / 参数 / 结果 / log)
│   └── 导出(后续)
├── /settings                      # 配置
│   ├── shell(超时 / 并发 / 编码)
│   ├── audit(保留 / 容量)
│   ├── ui(主题 / 自动开浏览器)
│   └── windows_task(自启动开关)
└── /rules                         # 规则
    ├── 列表(builtin + custom)
    ├── 详情(命中次数 + 最后命中)
    ├── 启停
    └── 测试匹配(后续)
```

### 6.2 与后端 API

后端控制面 API（仅本机；共享 HTTP 模式使用本机 secret、Origin/Host 校验，浏览器 UI 另有 CSRF 防护）:

```text
GET    /api/status                  # server 状态
GET    /api/audit?agent=&tool=&ok=&from=&to=&page=&page_size=
GET    /api/audit/{id}              # 单条详情
GET    /api/audit/{id}/log          # 读 log 文件(分页)
POST   /api/settings                # 改配置(部分更新)
POST   /api/rules/reload            # 热加载
GET    /api/rules                   # 列出所有规则
PATCH  /api/rules/{id}              # 启用 / 停用
GET    /api/backgrounds             # 后台进程列表
POST   /api/backgrounds/{id}/stop
GET    /api/mcp-config-snippet      # 返回 mcp.json 配置片段
POST   /api/shutdown                # 优雅关闭 server
```

### 6.3 Angular 项目结构

```text
ui/
├── angular.json                    # outputPath: ../src/localmcptools/ui_assets
├── package.json
├── src/
│   ├── index.html
│   ├── main.ts
│   ├── styles.css
│   └── app/
│       ├── app.config.ts           # provideRouter + provideHttpClient
│       ├── app.routes.ts
│       ├── app.component.ts        # 顶层壳(侧栏 + 内容)
│       ├── app.component.html
│       ├── core/
│       │   ├── api.service.ts
│       │   ├── models.ts
│       │   └── interceptors/
│       │       └── error.interceptor.ts
│       ├── features/
│       │   ├── dashboard/
│       │   │   ├── dashboard.component.ts/html/css
│       │   │   └── widgets/
│       │   │       ├── server-status.widget.ts
│       │   │       ├── backgrounds.widget.ts
│       │   │       ├── ports.widget.ts
│       │   │       └── recent-calls.widget.ts
│       │   ├── audit/
│       │   │   ├── audit-list.component.ts/html/css
│       │   │   ├── audit-detail-drawer.component.ts
│       │   │   └── log-viewer.component.ts
│       │   ├── settings/
│       │   │   └── settings.component.ts/html/css
│       │   ├── rules/
│       │   │   ├── rules-list.component.ts/html/css
│       │   │   └── rule-detail.component.ts
│       │   └── mcp-config/
│       │       └── mcp-config.component.ts/html/css
│       └── shared/
│           ├── components/
│           │   ├── status-badge.component.ts
│           │   ├── time-ago.component.ts
│           │   └── bytes-pipe.component.ts
│           └── pipes/
│               ├── time-ago.pipe.ts
│               └── bytes.pipe.ts
```

### 6.4 关键 UI 细节

**Audit 详情抽屉**:
- 入参 + 出参(脱敏后)
- 错误码 + 错误信息 + suggestion
- 被拦的话高亮"Blocked by: <rule_id>"
- "查看 log" 按钮 → 弹出 log viewer, 虚拟滚动避免大文件卡

**Settings 实时生效**:
- 大部分字段(超时 / 并发 / 编码)保存后立即生效
- `server.port` / `host` 改完提示需重启
- UI 顶部显示"已重启 / 待重启"指示

**MCP 配置片段页**:
- 显示当前 server 端口
- 一键复制按钮 → 复制这段到用户 VS Code `mcp.json`:
```jsonc
{
  "servers": {
    "localmcptools": {
      "url": "http://127.0.0.1:7890/mcp",
      "type": "http"
    }
  }
}
```
- 显式标注路径(Code / Insiders 分别给)

**主题**:
- 用 Angular Material 的主题系统
- 跟随系统的实现在 `ui.theme='system'` 时监听 `prefers-color-scheme`

---

## 7. 实施阶段拆分

> 每个阶段都列**目标 + doD(完成定义)**,验收后进入下一阶段。

### 阶段 0 — 兼容性与安全 spike(预估 1 天)

**目标**: 用真实目标客户端确认 MCP transport、官方 SDK API 和最小安全边界。

**DoD**:
- [ ] 对 codebuddy、Copilot 分别验证 stdio 工具发现、调用、结构化结果与取消
- [ ] 只有在需要共享 server 时验证 Streamable HTTP、Origin 校验与本机 secret
- [ ] 锁定官方 MCP SDK、FastAPI 和实际 ASGI 集成 API；不沿用未验证的 import/path
- [ ] `workspace.inspect` 只读 tool 注册成功，且可被 `observe` profile 调用
- [ ] policy 拒绝和 `approval_required` 的最小 envelope 测试跑通

### 阶段 1 — workspace 诊断 + artifact + audit(预估 2 天)

**目标**: 不授予执行权限，也能让 agent 看懂项目、读取证据并稳定恢复。

**DoD**:
- [ ] `environment.get` 返回 OS / PS / 编码 / 语言 / 当前用户
- [ ] workspace 注册、canonical path 校验和 `workspace.inspect` 跑通
- [ ] `workspace.search_text` / `fs.read_range` 不可越出 workspace
- [ ] output artifact、摘要、handle、tail/range/search 跑通
- [ ] 所有调用写入脱敏后的 audit.sqlite；artifact 有用户 ACL 和保留策略
- [ ] 稳定返回 `{ok, data, meta, error}`、`next_actions` 与 correlation/run id
- [ ] 单元测试覆盖脱敏、编码、路径逃逸与输出分页

### 阶段 2 — policy、批准与受控执行(预估 2 天)

**目标**: 服务端能力委托，而不是 agent 以参数绕过安全限制。

**DoD**:
- [ ] `observe` / `workspace_exec` / `managed_process` profile 就位
- [ ] user approval 绑定 workspace、动作摘要、过期时间并且一次性消费
- [ ] 预设 test/build/lint 先于受控 `shell.run_command` 开放
- [ ] shell 强制 canonical cwd、环境变量和 profile allowlist；默认拒绝网络下载执行/提权
- [ ] 规则加载 + 匹配引擎作为纵深防御
- [ ] 内置 10 条规则全部就位
- [ ] 被拦时返回结构化错误(含 suggestion)
- [ ] audit log 记录拦截事件
- [ ] `rule_hit_stats` 表自动累加
- [ ] 单元测试: 每条规则至少 1 个正向 + 1 个反向用例

### 阶段 3 — 受管后台进程 + 端口管理(预估 1.5 天)

**目标**: agent 启 dev server 后能继续干别的, 能管端口。

**DoD**:
- [ ] `process.start_dev_server` / `get_status` / `stop_managed` / `list_managed` 跑通
- [ ] 后台进程状态自动同步到 SQLite
- [ ] server 退出时清理非 persistent 进程
- [ ] 用 Windows Job Object 绑定进程所有权；崩溃/重启后 reconcile 状态
- [ ] `list_listening_ports` / `find_by_port` 只读可用；不提供任意 PID kill
- [ ] 一个 demo: 起 `python -m http.server` → 查端口 → 关掉

### 阶段 4 — Angular UI 基础(预估 2 天)

**目标**: UI 能打开, 能看 audit, 能改 settings。

**DoD**:
- [ ] Angular 19 项目脚手架就位
- [ ] `npm run build` 输出落到 `src/localmcptools/ui_assets/`
- [ ] Dashboard 页显示 server 状态 + 后台进程 + 端口 + 最近调用
- [ ] Audit 页列表 + 详情抽屉 + log viewer(虚拟滚动)
- [ ] Settings 页编辑 + 保存(实时生效, 端口提示重启)
- [ ] MCP Config 页一键复制
- [ ] 自动开浏览器(`ui.auto_open_browser=true`)

### 阶段 5 — UI 自动化（可选 profile，预估 1.5 天）

**目标**: 在用户明确授权的窗口内，不靠截图拿到结构化 UI 树。

**DoD**:
- [ ] `interactive_ui` 默认关闭；用户选择目标窗口/进程后才允许调用
- [ ] `ui.get_ui_tree` 返回 JSON 树，排除凭据和受保护系统窗口
- [ ] `ui.find_element` 按 text / automationId / controlType 查找
- [ ] `ui.click_element` / `ui.type_text` 可用
- [ ] `ui.screenshot_full_screen` / `screenshot_window` / `screenshot_region` 备用
- [ ] VS Code 窗口 demo: 打开 Settings → 找到 "Auto Save" → 改成 "afterDelay"
- [ ] 单元测试 + 集成测试

### 阶段 6 — 面向弱 agent 的增强能力(预估 2 天)

**目标**: 减少弱 agent 自行构造 shell 命令、猜诊断路径和解析日志的需求。

**DoD**:
- [ ] `fs.read_range` / `fs.tail_log_file` / `fs.grep_files`
- [ ] `vscode.get_problems` / `get_installed_extensions` / `get_logs` / `get_debug_sessions`
- [ ] `runtime.detect_runtime` / `get_env` / `list_path`
- [ ] `workspace.run_test` / `workspace.build` / `workspace.git_status` 使用项目 profile 返回标准摘要
- [ ] `diagnostics.collect` 聚合运行时、Git、Problems、端口和最近失败 run，并给出 `next_actions`
- [ ] `diagnostics.explain_failure` 依据受控 artifact 返回错误分类、关键证据与恢复步骤

### 阶段 7 — UI 规则管理 + 打包(预估 1 天)

**目标**: 用户能在 UI 里管规则, 安装包可分发。

**DoD**:
- [ ] Rules 页: 列表 + 详情 + 启停 + 测试匹配
- [ ] `localmcptools install` 创建计划任务(用户级)
- [ ] `localmcptools uninstall` 清理
- [ ] README 完整说明

### 阶段 8 — 在三个 agent 上验证(预估 1 天)

**目标**: codebuddy、Copilot 都能用, workbuddy / minimax code 留接口。

**DoD**:
- [ ] codebuddy 配置 → 能列出 tools → 跑 `environment.get`
- [ ] Copilot 同上
- [ ] 两个 agent 并发调用 audit log 正确
- [ ] 文档: "如何配置这三个 agent"

---

## 8. 关键依赖锁定

```text
# requirements.txt(初步)
mcp>=1.0,<2      # 阶段 0 的兼容性 spike 后锁定实际版本
fastapi==0.115.0
uvicorn[standard]==0.30.0
pydantic==2.9.0
psutil==6.0.0
chardet==5.2.0
uiautomation==2.0.18
python-dotenv==1.0.0

# requirements-dev.txt
pytest==8.3.0
pytest-asyncio==0.24.0
ruff==0.6.0
mypy==1.11.0
```

具体小版本随阶段推进时锁定。

---

## 9. 风险与回退

| 风险 | 触发条件 | 回退方案 |
|---|---|---|
| FastMCP 与 FastAPI 集成不稳 | 跑不起来 / 路由冲突 | 退到 mcp.server 单独进程, UI 单独进程, 用文件 socket / 端口转发 |
| uiautomation 装不上 / API 难用 | 装 import 失败 | 退到 pywinauto |
| Angular 19 与 Angular Material 不兼容 | 构建失败 | 改 PrimeNG |
| 计划任务在某些 Win11 上不触发 | 用户登录后 server 不启动 | 提供"开机启动文件夹" .lnk 脚本作为 fallback |
| SQLite WAL 在网络盘上出问题 | %APPDATA% 在 OneDrive 同步 | 检测后提示用户改路径, 提供 `LMCP_DATA_DIR` 环境变量 |

---

## 10. 待确认事项

1. Angular 版本(草案 19, 是否同意)
2. UI 组件库(Material vs PrimeNG, 草案 Material)
3. 是否做 `localmcptools install`(计划任务) vs 提供 `.lnk` 文件让用户自己放启动文件夹
4. `audit.log_path` 是否需要 redact(密码 / token)
5. 是否需要 export CSV / JSON 功能
6. `mcp.json` snippet 是否要给两种 transport(HTTP + stdio)

以上 6 项拍板后,阶段 0 可以立刻开干。
