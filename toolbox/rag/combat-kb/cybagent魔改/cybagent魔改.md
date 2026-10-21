# cybagent 魔改知识库

CyberStrikeAI（本机代号 cybagent）在 Windows 上的部署 / 魔改 / 调优全记录。
**工程路径：`D:\cyberstrikeai`**

---

## 0 · 30 秒速查

```powershell
# 启动（双击 D:\cyberstrikeai\start.bat 也行）
D:\cyberstrikeai\start.bat

# 访问
http://127.0.0.1:8080/          账号：admin（密码见第 2 节「凭据」）

# 改配置后必须重启（不热加载）
Get-Process cyberstrike-ai -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3
Start-Process "D:\cyberstrikeai\start.bat" -WindowStyle Minimized
```

| 项 | 值 |
|---|---|
| 工程 | `D:\cyberstrikeai` |
| 编译 | Go 1.27.1 + MinGW GCC 16.2.0（`C:\mingw64`），**必须 cgo** |
| 产物 | `cyberstrike-ai.exe` ≈ 134 MB |
| 主站 / MCP | `127.0.0.1:8080` / `127.0.0.1:8081`（MCP 默认关） |
| 数据库 | `data\conversations.db`（用量表 `model_token_usage`） |
| 默认模型 | `deepseek-flash`（通道 `deepseek`），思考强度 `medium` |
| 已修复 | ⭐ 提示前缀缓存命中率 **0% → 96~99%** |

---

## 1 · 构建与启动

### 编译

```powershell
cd D:\cyberstrikeai
go build -o cyberstrike-ai.exe cmd/server/main.go   # ~2 分钟，产物 ~134MB
```

覆盖前**必须先停服务**（Windows 不能覆盖运行中的 exe）。

换机器时的环境：

```powershell
go env -w GOPROXY=https://goproxy.cn,direct
go env -w GOSUMDB=sum.golang.google.cn
go env -w CGO_ENABLED=1
go env -w CC=gcc
[Environment]::SetEnvironmentVariable("PATH", "C:\mingw64\bin;" + [Environment]::GetEnvironmentVariable("PATH","User"), "User")
```

> **必须 cgo**：`github.com/mattn/go-sqlite3` 无纯 Go 实现。
> `CGO_ENABLED=0` 能编译（~93MB），但一启动就报 `... 'CGO_ENABLED=0' ... This is a stub`。
> **93MB 的 exe 就是错的，正确的是 ~134MB。**

### 启动（`start.bat` 内容）

```bat
@echo off
chcp 65001 >nul
cd /d "D:\cyberstrikeai"
title CyberStrike AI
echo Starting CyberStrike AI on http://127.0.0.1:8080/
"D:\cyberstrikeai\cyberstrike-ai.exe" --http
echo.
echo Server exited with code %ERRORLEVEL%.
pause
```

成功标志：控制台出现 `● ONLINE   http://127.0.0.1:8080/` 和 `任务进程隔离已就绪`（`backend: windows_job`）。黑窗口别关。

### ⚠️ `0xc0000142` —— 不要用 Start-Process 直接起 exe

**失败方式**：`Start-Process exe -WindowStyle Minimized` / `-RedirectStandardOutput` / `.NET ProcessInfo{CreateNoWindow=$true}` → 进程秒退、端口不监听、`log\diagnostic-*.log` 记：

```
{"level":"fatal","caller":"logger/logger.go:81","msg":"进程隔离启动检查失败",
 "error":"exit status 0xc0000142","stacktrace":"...cmd/server/main.go:139"}
```

**原因**：`main.go:136` 有无条件启动自检 `processguard.Check()`（`internal/processguard/check.go:29`），它会 spawn 自己跑 `-h`；`0xc0000142`=`STATUS_DLL_INIT_FAILED`，只在父进程被 ShellExecute/异常 stdio 创建时触发。
**解法**：让 exe 经 `cmd.exe` 创建 → **用 `start.bat`**。该自检无法用配置关闭。

### 数据 / 日志

| 路径 | 说明 |
|---|---|
| `data\conversations.db` | SQLite 主库（对话/用户/资产/漏洞/用量） |
| `log\diagnostic-YYYY-MM-DD.log` | warn/fatal 日志（JSON Lines；`log.output: stdout`，info 只进黑窗口） |

---

## 2 · 配置与凭据

配置文件 `D:\cyberstrikeai\config.yaml`（gitignore，改完**必须重启**）。

### AI 通道（模型下拉框的来源）

```yaml
ai:
  default_channel: deepseek
  channels:
    deepseek:                      # DeepSeek Flash（默认）
      provider: openai_compatible
      base_url: https://api.deepseek.com/v1
      api_key: <填在 config.yaml，勿外传>
      model: deepseek-flash
      reasoning: { mode: on, effort: medium, allow_client_reasoning: true, profile: deepseek_compat }
    deepseek-pro:                  # DeepSeek V4 Pro
      base_url: https://api.deepseek.com/v1
      api_key: <填在 config.yaml，勿外传>
      model: deepseek-v4-pro
      reasoning: { mode: on, effort: medium, profile: deepseek_compat }
    qwen-max:                      # 弃用：key 是占位符 sk-xxxxxxx，选中必 401
```

**key 与 base_url 必须配套**：`sk-`+32位十六进制，DeepSeek 和 DashScope 格式一模一样，肉眼分不出。拿 DeepSeek 的 key 打 DashScope 地址 → `401 invalid_api_key`。

| 服务 | base_url |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| 阿里云百炼 DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

### 思考强度

`ai.channels.<id>.reasoning.effort`，**合法值 `low | medium | high | max | xhigh`**（空=不指定）。实测 DeepSeek 全接受：low→203 / medium→204 / high→256 / max→256（思维链最长）reasoning tokens。
本机当前 **`medium`**；`allow_client_reasoning: true` → 界面「推理强度」下拉可临时覆盖，选「系统」用配置值。

### 服务端口

`server.host/port = 127.0.0.1/8080`；`mcp.host/port = 127.0.0.1/8081`（`enabled:false`）。
`127.0.0.1` 只本机可访问；对外需改 `0.0.0.0` 并放行防火墙，**且先改 admin 密码**。

### 知识库检索（当前关闭）

```yaml
knowledge:
  enabled: false
  base_path: C:/Users/20751/Desktop/知识库   # ⚠️ 旧路径，启用前要改
  embedding: { model: text-embedding-v4, base_url: https://dashscope.aliyuncs.com/compatible-mode/v1, api_key: sk-xxxxxxx }
```

- `base_path` 用**绝对路径**（代码直接 `os.MkdirAll`/`WalkDir`，相对路径是相对**进程工作目录**）
- 启用需**真实 embedding key**；**DeepSeek 不提供 embedding 接口**，得用 DashScope 等

### 凭据（敏感）

| 用途 | 值 / 去哪里找 |
|---|---|
| Web 登录 | 用户名 `admin`；密码见 `data\conversations.db` 的 `rbac_users` 表（或问管理员） |
| DeepSeek API key | 见 `config.yaml` 的 `ai.channels.deepseek.api_key` |

> ⚠️ 本文件**不记录**明文 key / 密码。密码存 `rbac_users`（bcrypt `$2a$10$`）；重置：停服务后 `.\cyberstrike-ai.exe -reset-admin-password`。

---

## 3 · ⭐ 缓存命中率修复（核心）

### 现象

`model_token_usage` 里同一会话连续多轮，prompt 每轮增长却 **cached 恒为 0**：

| 时间 | prompt | cached | 命中率 |
|---|---|---|---|
| 15:53 | 27566 | 0 | 0.0% |
| 15:54 | 28181 | 0 | 0.0% |
| 15:58 | 28917 | 0 | 0.0% |
| 16:01 | 29677 | 0 | 0.0% |

### 排除法

| 假设 | 结论 |
|---|---|
| 程序读错字段 | ❌ 排除（`DeepSeek → go-openai SDK → Eino acl/openai adapter → eino_run_usage_accumulator → DB` 链路完整） |
| DeepSeek 没开缓存 | ❌ 排除（自动缓存） |
| 缓存 TTL 太短 | ❌ 排除（实测间隔 130s 仍命中 88.9%） |
| 系统提示有动态内容 | ❌ 排除（静态拼接） |
| 工作目录块 | ❌ 排除（按会话 ID 确定性生成） |

**实测**：真实 key 直连 DeepSeek，同前缀连打 3 次 → 第 2、3 次命中 **88.8%**。→ 机制正常，是**程序每轮发的前缀在变**。

### 根因

`internal/mcp/server.go` 工具列表来自 **map 迭代**：

```go
func (s *Server) GetAllTools() []Tool {
	tools := make([]Tool, 0, len(s.toolDefs))
	for _, tool := range s.toolDefs {   // toolDefs 是 map[string]Tool
		tools = append(tools, tool)      // Go map 迭代顺序【每次调用都随机】
	}
	return tools
}
```

该列表传到 `internal/multiagent/eino_tool_name_injection.go`，被拼成**"工具名索引"放在系统提示最前面**：

```
以下是当前会话绑定的工具名称索引（仅名称，无参数 JSON Schema）。
- write_file
- read_file
- ...            ← 顺序每轮都变
```

→ **系统提示第一个字节每轮都变** → DeepSeek 前缀缓存整段作废 → 永远 0%。

### 修法（只排序，不改内容）

**1. `internal/agent/agent.go`**（`getAvailableTools()` 返回前，根治点）：新增 `import "sort"`，并加：

```go
sort.SliceStable(tools, func(i, j int) bool {
	return tools[i].Function.Name < tools[j].Function.Name
})
```

**2. `internal/mcp/server.go`**（`GetAllTools()` 和 `handleListTools()` 各加一行）：

```go
sort.Slice(tools, func(i, j int) bool { return tools[i].Name < tools[j].Name })
```

**合计：3 文件 +7 行**。`sort` 在 `server.go` 已 import。

### 验证结果

| | 会话 | prompt | cached | 命中率 |
|---|---|---|---|---|
| 第 1 条 | 新会话 | 27535 | 3584 | 13.0%（冷启动） |
| **第 2 条** | 同会话 | 27856 | **27520** | **98.8%** ✅ |
| 浏览器第 2 条 | 同会话 | 28398 | **27520** | **96.9%** ✅ |

### 使用要点

命中率是"**同一会话内重复前缀**"的属性，不是单次调用的：

| 用法 | 命中率 |
|---|---|
| 频繁开新会话、一次一问 | 第 1 条恒为 13%（冷启动） |
| **同一会话连续多轮**（真实渗透就是这种） | **96~99%** |

**→ 一个任务从头到尾在同一个会话里问，别频繁点「+ 新任务」。**

### 仍会掉命中率（无法 100% 保证）

| 情况 | 原因 |
|---|---|
| 每个会话第 1 条 | 冷启动（无解） |
| 会话**绑定项目** | 代理写「项目黑板」事实 → 系统提示变 → 那几轮 miss |
| 中途切模型/通道 | 新模型独立缓存 |
| 中途换角色 | 工具集变 → tools hash 变 |
| 上下文压缩 | 前缀重写一次 |

> 系统提示还嵌了"本会话工作目录"（`...\conversations\<会话ID>\`），所以**跨会话**只蹭到 13%，**同会话**才 96%。

### reasonix 参照（`D:\reasonix源码\04-Reasonix源码\DeepSeek-Reasonix`）

- `internal/agent/cache_shape.go` —— `CaptureShape` 哈希(系统提示+工具)，`normalizeToolSchemas` 排序后哈希
- `internal/agent/cache_shape_test.go` —— 跨 schema 顺序哈希不变
- `internal/tool/registry_test.go` —— `TestRegistrySchemasSorted` 强制工具字母序
- `.github/workflows/ci.yml` —— CI 跑 `TestCacheHit*` 前缀稳定性守卫

核心思想：**系统提示 + 工具 schema 冻结成会话级"字节稳定前缀"，动态内容走 user 轮或按需检索。**

---

## 4 · 故障排查速查

| 症状 | 原因 | 解决 |
|---|---|---|
| 启动崩 `...0xc0000142` | 用 Start-Process 直接起 exe | 用 `start.bat` |
| 启动崩 `... This is a stub` | 无 cgo 构建 | `CGO_ENABLED=1 CC=gcc` 重编译 |
| `go build` 找不到 gcc | MinGW 不在 PATH / 旧环境 | 新开终端；确认用户 PATH |
| 对话 `401 invalid_api_key` | key 占位符 / key 与 base_url 不配套 | 见第 2 节 |
| 模型下拉框缺模型 | 不在 `ai.channels` | 加通道→重启→刷新 |
| 改配置不生效 | 不热加载 | 重启 |
| 端口不监听但进程在 | 启动自检 Fatal | 看 `log\diagnostic-*.log` 末尾 |
| **命中率恒 0%** | 工具列表顺序随机 | 见第 3 节（已修） |
| 命中率仅 13% | 新会话第 1 条冷启动 | 正常，同会话续问才 96% |
| curl 访问 8080 超时 | 系统代理拦回环 | 加 `--noproxy "*"` |
| 重置密码失败 | 端口被占 | 先停服务 |
| 新机器跑不起来 | 缺 MinGW DLL | `libgcc_s_seh-1.dll`、`libwinpthread-1.dll` 放 exe 同目录 |
| 日志 `sonic/ast ... fallback` | Go 1.27 超 sonic 支持 | 非致命，忽略 |
| Select-String 出乱码 | 扫到 134MB exe | 加 `-Include *.go,*.yaml,*.md` |
| 源码文件消失 | Defender 隔离 | `git checkout -- "<文件>"` + 加排除项 |

**诊断三件套**

```powershell
Get-Process cyberstrike-ai -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
Get-ChildItem D:\cyberstrikeai\log\diagnostic-*.log | Sort LastWriteTime -Desc | Select -First 1 |
  ForEach-Object { Get-Content $_.FullName -Encoding UTF8 -Tail 1 }
```

**退出码**：`0xc0000142`=DLL 初始化失败；`0xc0000135`=缺 DLL；`1`=应用主动 Fatal。

**用量查询**

```powershell
python -c "import sqlite3; c=sqlite3.connect(r'file:D:/cyberstrikeai/data/conversations.db?mode=ro',uri=True); [print(r) for r in c.execute('select created_at,conversation_id,prompt_tokens,cached_tokens from model_token_usage order by created_at desc limit 10')]"
```

---

## 5 · 变更台账

| 时间 | 变更 |
|---|---|
| 2026-10-01 | 定位命中率根因（map 迭代 → 工具名索引乱序） |
| 2026-10-01 | 改 3 文件 +7 行 → 重编译 → 实测 96.9% / 98.8% |
| 2026-10-01 | 清理修复前 4 条 0% 脏统计（备份 `data\conversations.db.bak-20261001-212145`） |
| 2026-10-01 | 思考强度 high → **medium**（deepseek + deepseek-pro） |
