# LocalMcpTools — 需求文档

> 状态: **v0.1 草案**
> 最后更新: 2026-08-07
> 作者: 用户提需求, GitHub Copilot 协助整理

---

## 1. 背景

### 1.1 问题陈述

当前在 VS Code 中使用 **codebuddy** 和 **GitHub Copilot** 等 agent 辅助开发时,经常出现以下几类"卡死"现象:

| 现象 | 频次 | 根因 |
|---|---|---|
| agent 调用 shell 命令后**没有返回结果** | 高 | 输出截断 / stdout 与 stderr 混在一起 / exit code 丢失 / 编码乱码 |
| agent 执行了**危险操作**(误删、误格式化、误改注册表) | 中-高 | 没有拦截机制, agent 推理链路里出一步错就执行了 |
| agent **启了 dev server 后忘记停**,后续任务全报端口占用 | 高 | 没有进程生命周期管理 |
| agent 在 Chinese Windows 下拿到 **GBK 乱码** | 高 | 编码未显式声明为 UTF-8 |
| agent **图像识别能力差**, UI 调试卡住 | 中 | 没有结构化的 UI 树,只能靠截图 |
| agent 报错后**没有线索**复现问题 | 高 | 没有调用记录可查 |

这些问题的共同点: **不是 agent 模型本身的问题,而是缺少可靠的本地基础设施**。

### 1.2 现成方案的不足

| 方案 | 不足 |
|---|---|
| VS Code 自带 terminal | 输出截断、无 audit、agent 拿到的字段不稳定 |
| 各 agent 自带的 shell 工具 | 不通用(换 agent 全部重来)、各家实现参差不齐 |
| 直接给 agent `cmd` 权限 | 无安全网, 出错代价大 |
| 截图类 UI 工具 | agent 看不到图就废 |

### 1.3 项目目标

**为本地 agent 提供一套稳定、安全、可观测的受控开发工作台（通过 MCP 暴露）**。server 持有宿主机权限，agent 只获得用户预先委托的、范围受限的能力，而不是一个可审计的全权限终端。

覆盖:

1. 环境诊断(操作系统、PowerShell 版本、编码)
2. 可靠的 Shell 执行(超时可控、编码正确、输出完整、错误可追溯)
3. 高风险命令拦截(可配置、可审计)
4. 后台进程 / 端口管理
5. UI 自动化(基于 Windows UI Automation 的结构化元素树)
6. VS Code 专用诊断(Problems 面板、扩展、日志、debug session)
7. 文件 / 日志读取增强(范围读、tail、grep)
8. 工具链探测(python / node / dotnet / msbuild)

### 1.4 产品原则

1. **能力委托而非命令转发**：权限由服务端 policy/profile 决定，不能由 agent 的 `allow_dangerous` 参数自行提升。
2. **工作区优先**：有副作用的操作必须归属到已注册的 workspace；默认不能读写或执行 workspace 外的内容。
3. **先观察，后建议，再执行**：默认只读诊断；可能有副作用的操作先给出计划与影响范围，按策略或用户批准后执行。
4. **语义化工具优先**：为测试、构建、搜索、诊断、受管 dev server 提供稳定工具；受控 shell 仅是明确授权的逃生口。
5. **输出可消费**：大输出写入受保护的 artifact，以摘要、分页和 handle 返回，不能把 MB 级日志直接塞进 agent 上下文。
6. **失败可恢复**：所有失败均返回稳定错误码、证据和下一步建议；用户可以在 UI 中回放、批准或排查。

---

## 2. 目标用户

| 角色 | 场景 |
|---|---|
| **直接使用者**(本人) | 在 VS Code 里用 codebuddy / Copilot,希望 agent 跑命令稳定、出错可查 |
| **未来的自己** | 接 workbuddy、minimax code 时不用重新搭一套基础设施 |
| **未来可能的其他用户** | 同样受 VS Code agent 工具问题困扰的开发者 |

---

## 3. 范围

### 3.1 In-Scope(本次必做)

| 项 | 说明 |
|---|---|
| 实现语言 | Python |
| 平台 | Windows(预留 Linux / macOS 接口签名,实现可空) |
| IDE | VS Code |
| 目标 agent | codebuddy、GitHub Copilot; 预留 workbuddy、minimax code |
| MCP 传输 | stdio 默认；需多 agent 共享时采用 Streamable HTTP |
| 后台常驻 | 共享 HTTP 模式可由用户启动并复用；stdio 模式随客户端启动 |
| 数据持久化 | SQLite + 本地文件 |
| UI | 共享 HTTP 模式提供浏览器 UI；stdio 模式可不启动 UI |
| 安全 | 服务端 capability profile、workspace scope 与短时用户批准 |
| 审计 | 所有调用记 audit log；输出以脱敏后的受保护 artifact 保存 |
| 权限模型 | 服务端 capability profile + workspace scope + 短时用户批准 |
| 默认模式 | `observe`（只读）；执行能力须显式配置 |

### 3.2 Out-of-Scope(本次明确不做)

| 项 | 理由 |
|---|---|
| Linux / macOS 实际实现 | 用户当前只用 Windows |
| 非 VS Code IDE 支持 | 暂不涉及 |
| 远程访问 server | 安全考虑, 默认只听 127.0.0.1 |
| 多用户 / 公共 OAuth / TLS | 单机工具；共享 HTTP 仍使用本机 secret 识别调用方 |
| 自动改用户 VS Code `mcp.json` | 避免与用户已有配置冲突 |
| 与具体 agent 的深度耦合 | 只走标准 MCP 协议 |
| 将 agent 提供的 `cwd`、`agent`、`allow_dangerous` 当作可信权限依据 | 这些字段只能作为请求信息，不能提升权限 |
| 任意宿主机 shell / 文件系统 / PID / 桌面自动化 | 仅以受限 profile 和资源所有权方式开放 |

---

## 4. 核心场景与验收标准

### 4.1 场景 A: agent 跑任意命令能拿到完整结果

**流程**:
1. agent 在已注册 workspace 中调用 `workspace.run_test(timeout_ms=120000)`
2. server 执行, stdout + stderr 都正确编码为 UTF-8 返回
3. 返回 `{ok: true, data: {exit_code: 0, stdout: "...", stderr: ""}, meta: {duration_ms: 45230, log_path: "...", audit_id: "..."}}`

**验收**:
- [ ] 中文 Windows 下输出无乱码
- [ ] 长输出不会占满 MCP/模型上下文；返回摘要、字节数、`output_handle` 和明确的 `truncated` 状态
- [ ] agent 可通过 `output.tail` / `output.read_range` / `output.search` 分页取得所需证据
- [ ] 超时返回 `timed_out: true`, 不抛异常
- [ ] exit code 正确, 不为 0 时 `ok: false`
- [ ] 原始输出有 log 文件路径可查

### 4.2 场景 B: 误操作被拦截

**流程**:
1. agent 请求执行 `Format-Volume -DriveLetter C`
2. server 命中 `block-format-volume` 规则, 返回 `{ok: false, error: {blocked_by: "block-format-volume", severity: "critical", suggestion: "..."}}`
3. audit log 记录拦截事件

**验收**:
- [ ] critical 级规则不可由 tool 参数或 agent 绕过；必要时只能由用户在本地 UI/CLI 的独立恢复流程处理
- [ ] 拦截原因 human-readable, 附 suggestion
- [ ] UI 能看到拦截历史 + 触发频率

### 4.3 场景 C: 启 dev server 后 agent 继续干活

**流程**:
1. agent 调用 `process.start_dev_server(workspace_id, preset="python-uvicorn")`，或在获授 `workspace_exec` 能力后调用受控执行
2. server 返回 `{id: "bg-001", pid: 12345, log_path: "..."}`
3. agent 继续做别的, 随时 `shell.get_status("bg-001")` 查状态
4. agent 改完代码后调用 `shell.tail_log("bg-001", n=50)` 看 server 输出

**验收**:
- [ ] `start_background` < 1s 返回
- [ ] `list_backgrounds` 能列出所有由本 server 启动的后台进程
- [ ] server 退出时未标记 `persistent: true` 的后台进程被清理
- [ ] 端口冲突可由只读查询与 `process.stop_managed(id)` 处理；不能按任意 PID 杀宿主机进程

### 4.4 场景 D: UI 调试不靠截图

**流程**:
1. agent 收到"启动后没看到窗口"的报告
2. 调用 `ui.get_ui_tree()` 拿到结构化元素树
3. 调用 `ui.find_element_by_text("Submit")` 定位按钮
4. 调用 `ui.click_element(automationId="btnSubmit")` 操作

**验收**:
- [ ] `get_ui_tree` 返回 JSON, agent 不需要图像识别
- [ ] 元素查找支持 text / automationId / controlType / name 多条件
- [ ] 点击 / 输入操作返回 success / element-not-found 等明确状态

### 4.5 场景 E: 用户通过 UI 排查 agent 出错

**流程**:
1. agent 反馈"我没拿到结果", 用户打开 UI `http://localhost:7890/ui/`
2. UI 显示最近 200 条调用, 用户按 `agent=copilot, ok=false` 过滤
3. 用户点开某条, 看到命令 / 参数 / 耗时 / log 路径 / 是否被拦
4. 用户点 "view log", 在 UI 里看原始输出(或下载)
5. 用户改 settings 调高超时, 保存, 不用重启 server

**验收**:
- [ ] UI 列出最近调用, 支持多条件过滤
- [ ] 单条详情能看到完整入参 + 出参(脱敏)+ 错误原因
- [ ] 设置修改后立即生效(或明确告知需要 reload)
- [ ] log 文件可在线查看 / 下载

### 4.6 场景 F: 权限有限的 agent 完成开发闭环

1. agent 调用 `workspace.inspect` 获得已允许 workspace、Git 状态、项目类型、可用预设与运行时缺失项。
2. agent 调用 `workspace.search_text`、`diagnostics.collect` 和 `workspace.run_test`；每个结果携带 `evidence` 与 `next_actions`。
3. 若操作会写文件、安装依赖、启动长期进程或超出默认 profile，server 返回 `approval_required`，并给出精确影响范围。
4. 用户在本地 UI/CLI 对本次操作批准；批准绑定 workspace、能力、命令/预设摘要和过期时间。
5. agent 使用批准 token 完成操作，审计记录 policy、approval 和产物。

**验收**:
- [ ] `observe` profile 不可能执行写文件、任意命令、任意 PID kill 或 UI 输入
- [ ] 受控执行不能离开注册 workspace，且命令、环境变量和网络策略受 profile 限制
- [ ] 被拒绝/需批准时有机器可读 error code、human-readable 原因和可执行的下一步
- [ ] 同一任务的诊断、执行和日志 artifact 由 correlation/run id 串联，可在 UI 回放

### 4.7 面向能力欠缺 agent 的能力包

这些能力包优先补足“理解项目、取得证据、稳定执行、从失败恢复”四类短板。每一项都必须受 profile 与 workspace 约束，不因为 tool 存在就默认授予所有 agent。

| 能力包 | 解决的短板 | 首批工具/行为 | 建议 profile / 优先级 |
|---|---|---|---|
| 项目理解 | 不会判断仓库类型、入口和当前状态 | 项目类型、Git 状态、运行时、脚本、测试入口、允许目录的结构化摘要 | `observe` / P0 |
| 代码与日志取证 | 搜索不准、长输出读不完、拿不到错误关键行 | 范围读、受限 grep、输出 handle、tail、错误上下文和来源位置 | `observe` / P0 |
| 诊断与恢复 | 失败后只会反复重试 | 编译/测试/端口/运行时/VS Code Problems 汇总；稳定错误码、证据和 `next_actions` | `observe` / P0 |
| 标准开发动作 | 不会正确拼 test/build/lint 命令 | 依据项目 profile 的 `run_test`、`build`、`lint`、`git_status` 预设 | `workspace_exec` / P1 |
| 受管运行时 | 忘记关闭 dev server、不会定位端口 | 启动预设 dev server、健康检查、日志、停止本 server 受管进程 | `managed_process` / P1 |
| 变更前检查 | 不会评估副作用 | 展示将执行的预设/命令、cwd、影响文件、风险和所需批准 | `observe` + approval / P1 |
| IDE 诊断 | 看不到 Problems、debug 或扩展异常 | VS Code Problems、日志位置、debug session、扩展/版本摘要 | `observe` / P2 |
| 结构化 UI | 图像理解弱 | 已授权窗口的 UI 树、元素查找；点击/输入必须单独批准 | `interactive_ui` / P2 |
| 依赖与知识补足 | 不会判断缺失依赖或版本冲突 | 本地 lockfile/manifest 解释、运行时检查、离线文档索引；联网查询另设网络 profile | `observe` / P2 |

明确不提供“替 agent 做决策”的黑盒工具。工具应提供高质量、可定位的事实和受控动作，让 agent 仍能解释自己的判断、展示证据并在失败后恢复。

---

## 5. 非功能性需求

| 维度 | 目标 |
|---|---|
| 启动时间 | server 冷启动 < 3s |
| 工具调用延迟 | `run_command` 本地命令 < 100ms 开销(不含命令自身耗时) |
| 并发 | 同时 4 个 shell 命令不互相阻塞 |
| Audit 容量 | 默认保留 30 天, 默认上限 2GB, 可改 |
| 安全 | 不主动监听 0.0.0.0, 不写用户 VS Code 配置 |
| 编码 | 输入 / 输出统一 UTF-8, 兜底 GBK |
| 可观测性 | 所有调用 + 所有拦截都进 audit log |
| 可恢复性 | server 崩溃后重启, config / audit / rules 不丢 |
| 机密保护 | 在持久化前脱敏；日志/artifact 默认仅本用户可读；token 不进入 URL、审计或输出 |
| 资源保护 | 限制单次输出、文件搜索、并发、CPU/内存、子进程数与后台进程 TTL |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| PowerShell 中文环境编码不一致 | 命令输出乱码 | server 启动子进程时强转 UTF-8 + 自动编码探测 |
| agent 启动太多后台进程 | 资源耗尽 | 后台进程上限 + 定期清理 + UI 可见 |
| 高危规则误伤 agent 正常工作 | agent 无法完成合法任务 | 语义化预设 + profile/workspace 授权 + 用户短时批准；critical 永不由 agent 绕过 |
| Audit 日志无限增长 | 磁盘占满 | 30 天滚动清理 + 大小上限 + UI 上有"清理"按钮 |
| Server 异常退出 | 后台进程泄漏 | server 退出 hook + 所有 `persistent: false` 的子进程一起 kill |
| MCP 协议 / agent SDK 升级 | 兼容性破坏 | 锁定 MCP SDK 版本, 测试三个目标 agent 的兼容性 |
| 黑名单或 shell 解析被绕过 | 发生宿主机越权 | profile/workspace allowlist 为主；规则仅作纵深防御；测试编码命令、脚本、转义和路径逃逸 |
| 审计日志包含密钥 | token 或隐私泄露 | 落盘前脱敏、文件 ACL、短保留期、按身份授权查看 |
| 本地 HTTP 被网页或其他进程调用 | 发生未授权执行 | 默认 stdio；共享 HTTP 使用本地 secret、Origin/Host 校验和 UI CSRF 防护 |

---

## 7. 成功标准

项目"完成"的定义:

1. ✅ `shell.run_command` 在 Chinese Windows 下能正确返回中文输出
2. ✅ 高危命令被拦截, audit log 记录原因
3. ✅ UI 能启动, 能看到最近调用, 能改 settings
4. ✅ codebuddy 与 GitHub Copilot 均能通过 MCP 发现并调用工具
5. ✅ server 开机自启动(用户手动触发或计划任务)
6. ✅ `start_background` + `get_status` 跑通完整 demo
7. ✅ `ui.get_ui_tree` 能拿到 VS Code 窗口的结构化元素
8. ✅ `observe` profile 的 agent 能完成诊断与测试闭环，但不能越出 workspace 或执行未授权副作用
9. ✅ 执行类能力有 profile、workspace、policy version、run id 和（如需要）approval id 的可审计证据

---

## 8. 待确认 / 后续补充

- [ ] Angular 版本(建议 Angular 19)
- [ ] UI 主题(亮 / 暗 / 跟随系统)
- [ ] 是否需要中英双语 UI
- [ ] 审计日志是否需要 export 功能(优先级低)
- [ ] 是否需要打包成可执行文件(PyInstaller / Nuitka), 还是直接用 Python 脚本启动
- [ ] 第一批 capability profile：是否采用 `observe`、`workspace_exec`、`managed_process`、`interactive_ui` 四级
- [ ] workspace 注册入口：UI 选择目录、CLI 命令或两者同时支持
- [ ] 用户批准的交互：仅 UI、仅 CLI，或两者均支持；默认有效期建议 10 分钟
