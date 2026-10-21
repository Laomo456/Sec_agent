# 工具目录（热加载）

**每个子目录 = 一个工具**，目录名即工具名。server 每次检索/执行都重扫本目录：
新增工具丢一个新目录进来即可，不改框架代码、不重启 server。
也可以在网页「工具 → 工坊」里可视化创建（生成的工具带 `origin: user`，可在网页编辑/删除）。

## tool.yaml 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| name | ✓ | 工具名（与目录名一致，小写+下划线） |
| kind | | `script`（默认，run.py 实现）/ `http` / `command`（声明式，见下） |
| description | ✓ | 一句话描述（进工具卡，控制在 20 字内） |
| category | ✓ | 分类（如 basic / network / system） |
| danger | ✓ | readonly / intrusive / destructive。网关据此判断"写后读"是否需要重新执行 |
| keywords | ✓ | 中英关键词列表，**检索权重最高**；把用户可能说的动词都写上（写入/保存/存到…） |
| search_text | ✓ | 用法要点：什么时候用、有什么坑。进 describe_tool 与直连模式的工具说明 |
| schema | ✓ | JSON Schema（type=object），run_tool 按它校验参数 |
| example | 建议 | 最小可用参数示例（进工具卡，模型照抄最不容易错） |
| timeout | | 子进程超时秒数，默认 600 |
| requires | | 依赖的外部二进制名，缺失时返回 unavailable |
| op_danger | | 单卡多操作工具（如 `fnid`）的 逐操作 danger 表；网关据此对 `op=identify` 判只读、`op=train` 判写，mixed 工具不失真 |
| origin | | builtin（默认）/ user（工坊创建） |

## 三种实现方式

**script**：目录里放 `run.py`，约定 `python run.py '<params-json>'`，结果打印到 stdout。
文件头必加 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（GBK 控制台打印中文会崩）。

**http**（无需写代码）：
```yaml
kind: http
http:
  method: GET
  url: https://api.example.com/search
  query: {q: "{{query}}", limit: "{{limit|10}}"}   # {{参数}} / {{参数|默认值}}
  headers: {Authorization: "Bearer xxx"}
  body: {text: "{{text}}"}      # dict → JSON 发送；值恰好是 {{x}} 时保留原类型
  pick: data.items              # 可选：只返回 JSON 响应里的某个路径，省 token
  allow_error: false            # 4xx/5xx 是否视为成功
```

**command**（无需写代码）：
```yaml
kind: command
command:
  argv: [git, log, "-n", "{{n|5}}", "{{path}}"]   # 不经 shell；替换后为空的项自动省略
  stdin: "{{text}}"                               # 可选
```
程序名需在白名单内（python pip curl ping nslookup ipconfig whoami echo where findstr
git node npm tar powershell），工作目录为会话工作区。

## 统一结果信封

所有工具（内置/自建/Skill/外部 MCP）返回同一形状：

```json
{"status": "ok", "tool": "file_read", "call_id": "c003",
 "summary": "前几行摘要", "stats": {"lines": 12, "tokens_est": 80, "duration_ms": 41, "truncated": false},
 "output": "……"}
```
- 失败：`status` 为 error / timeout / validation_error（带 field + detail）/ unavailable / unknown_tool；
  非 0 退出额外带 `exit_code`
- 超 1500 token：`output` 换成 `output_head` + `output_tail` + `hint`，用 read_output 钻取
- 落盘路径不返回给模型，只记在审计 `workspace/<会话>/audit.jsonl`

## 现有工具

- `file_read` / `file_write` / `file_list` / `file_edit`：文件四件套（相对路径落会话工作区）
- `web_fetch`：HTTP 请求（curl，text_only 提正文）
- `run_cmd`：白名单命令执行
- secbox 迁移的安全工具 22 个（2026-09-29，`bin/` 自带二进制自包含）：
  recon：`nmap` `subfinder` `hakoriginfinder` `tls_inspect` `http_probe`；
  web：`ffuf` `katana` `dirsearch` `arjun` `dalfox` `sqlmap` `commix` `wafw00f` `mitm_proxy`；
  vuln：`nuclei` `fscan` `zap`；reverse：`packer_detect` `upx` `ysoserial`；misc：`interactsh`；crypto：`hash_calc`。
  其中 `arjun/commix/dirsearch/mitm_proxy/sqlmap/wafw00f` 为 pip 装进 gateway venv 的
  console 工具（run.py 经 `sys.executable` 同目录解析）；会话目录读 `AGENT_SESSION`。
- `fnid`：函数指纹识别（2026-10-01，桥接外部项目 `D:\RAG\fnid`）。单卡 8 操作
  `fnid(op, params)`：识别类只读（identify/predict/normalize/vocab/status），
  语料/训练类写（extract_go/extract_c/train）。torch/CUDA 在系统 `py`，
  gateway venv 无——run.py 是垫片，子进程转发（`FNID_HOME` 可换项目路径）。
- `oneforall`：子域收集一把梭（2026-10-08，`home/` 自带源码 + Python 3.10 venv）。
  被动聚合 + 字典爆破 + altdns 置换 + DNS 解析 + HTTP 存活 + 接管检查；
  brute/dns/req/takeover/alive_only 降档开关。要快速被动枚举先用 `subfinder`，
  要全/要爆破/要存活验证用本工具（全程 5~20 分钟，timeout 1500）。
  依赖 2022 年代包（gateway 3.14 装不上），run.py 垫片转发自带 venv
  （`ONEFORALL_HOME` 可换项目路径，重装见 `home/INSTALL.md`）。
- `android_unpack`：安卓脱壳（2026-10-15，MuMu+frida 运行时 dump 加固 app）。
  单卡 11 op `android_unpack(op, params)`：环境/模拟器/frida 部署幂等管理 +
  install/launch + attach 模式 dexdump（反检测三板斧固化）+ dex_scan 业务 dex
  定位与 API 端点提取。`bin/` 自带 adb + frida-server-17.x（**必须 17.x**：
  16.7.19 原版在 MuMu Android 15 上启动即注入 zygote，后续 fork 全崩），
  frida 库在 gateway venv，MuMu 路径走 `config.json`/`AGENT_MUMU_MANAGER`。
  dump 出的业务 dex 交 `jadx`（mcp）反编译。阶段记录 `../../docs/stage-android-unpack-tool.md`。
- `attack_reference`：CyberStrike 攻击方法论只读参考。`categories` 列分类，`search` 返回
  漏洞类型/技术栈/阶段的短证据卡，`read` 按 `path` 或 `skill` 分段读取有限正文，`status`
  查看本机 Skill 根目录。默认读取 `%USERPROFILE%/.local/share/cyberstrike/skill`，可用
  `CYBERSTRIKE_SKILL_ROOT` 覆盖。工具不执行文档命令、不扫描目标、不自动发请求；结果只作
  授权测试参考，实际验证仍调用现有安全工具。阶段记录 `../docs/stage-attack-reference.md`。

外部 MCP 源见 `../mcp.json`（默认为空）。
