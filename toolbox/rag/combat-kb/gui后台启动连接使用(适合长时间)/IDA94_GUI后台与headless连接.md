# IDA 9.4 — GUI 后台启动 / 连接 / 使用（适合长时间挂机）

> 场景：让 IDA 跑在后台（GUI 或 headless），AI / 脚本远程连接分析，不占终端。
> 版本：IDA Professional 9.4.260714
> 路径：`D:\IDA Pro 9.4.260714`

---

## 一、三种启动方式对比

| 方式 | 命令 | 有界面 | 适合 |
| :--- | :--- | :---: | :--- |
| GUI 前台 | `ida.exe <file>` | ✅ | 手动点点、看反编译 |
| GUI 后台 | 起 job 后台跑 `ida.exe <file>` | ✅ | 挂着等它分析完，人不守着 |
| headless | `idalib-mcp`（Python 库） | ❌ | AI/脚本远程反编译，长时间服务 |

**关键**：GUI 后台 和 headless 是并行的，互不干扰。GUI 占一份进程，headless 走 8745 端口另起服务。

---

## 二、GUI 后台启动

### 1. 前台启动（普通）
```bash
"D:\IDA Pro 9.4.260714\ida.exe" <目标文件>
```

### 2. 后台启动（不占终端，挂机）
```bash
start "" "D:\IDA Pro 9.4.260714\ida.exe" <目标文件>
```
或脚本里用 `run_background` / 任务计划起 job，返回 pid 即可。

### 3. 验证进程在跑
```bash
tasklist | findstr /i "ida.exe"
```
看到 `ida.exe <pid>` 即存活。内存占用几百 MB 正常。

### 4. 关闭后台 GUI
```bash
taskkill /pid <pid> /f
```

---

## 三、headless 连接（AI / 脚本用，适合长时间）

### 1. 启动 headless MCP 服务
```bash
uv --directory "D:\ida mcp\ida-pro-mcp1" run idalib-mcp --host 127.0.0.1 --port 8745
```
成功输出：
```text
[MCP] Server started
  Streamable HTTP: http://127.0.0.1:8745/mcp
  SSE:             http://127.0.0.1:8745/sse
```

### 2. 验证服务通
- GET `http://127.0.0.1:8745/mcp` → `405 Method Not Allowed`（**正常**，需 POST/SSE）
- GET `http://127.0.0.1:8745/sse` → `200 text/event-stream`（长连接，读取会挂起，正常）

### 3. Reasonix 注册（让 AI 工具列表出现 `ida-pro__*`）
在 Reasonix 里 add_mcp_server：
```text
name:      ida-pro-mcp
transport: sse
url:       http://127.0.0.1:8745/sse
```
写入 `C:\Users\20751\.reasonix\config.json`，**下次启动生效**。

### 4. 远程连接工具（AI 可用）
`idalib_open` / `idalib_switch` / `idalib_current` / `idalib_unbind` / `idalib_list` 等。

---

## 四、注意点

1. **SSE 读取会挂起** —— 它是长连接流，等事件推送。用脚本 curl/urlopen 会超时，这是正常现象，不是坏了。
2. **headless 服务要常驻才连得上** —— 停了就 405/连接拒绝。长时间挂机就让它一直在后台。
3. **headless 不锁 GUI 文件** —— 你可以同时用 GUI 看同一个二进制，互不影响。
4. **环境变量**：
   - `IDA_HOME` = `D:\IDA Pro 9.4.260714`
   - 用户 PATH 已含该目录（命令 `ida` 可直接调）
5. **license**：授权文件在 `Downloads\IDA Pro 9.4.260714\授权\`，本目录 `idapro.hexlic` 已激活。

---

## 五、踩坑记录

- `ida.exe -h` 返回非 0 —— 正常，GUI 程序对 -h 不友好；用 `idat.exe -h` 看帮助
- idalib 报 `import idapro` 失败 → 需手动 `pip install idalib\python\idapro-*.whl`（激活脚本只写配置，不装包）
- 9.4 API 改名：`get_inf_structure()` → 新接口；`init_ida` 不存在，用 `open_database()`