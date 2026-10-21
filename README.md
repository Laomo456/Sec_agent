# sec_agent — 专注渗透测试与多维度发掘

自研的 AI 安全智能体平台:一个 OpenAI 兼容的 agent 网关(FastAPI + 自研循环),
带 53 个热加载安全/逆向工具、22 个攻防技能、Dify 式可视化工作流引擎、RAG 知识库、
subagent 隔离委派和 Vue 3 前端。面向**授权渗透测试、CTF 竞赛与安全研究**。

```
┌────────────────────────────────────────────────────────┐
│  前端 Vue3 + Naive UI + Vue Flow(预构建, :8300 直连) │
├────────────────────────────────────────────────────────┤
│  gateway/  FastAPI + WS                                │
│    agent.py      自研循环:流式 / 中途插话 / 上下文纪律 │
│                  (持久化记忆 · 失败回滚墓碑 · 防兜圈)   │
│    workflow.py   Dify 式 DAG:并行分支 / iteration /    │
│                  loop / 错误三分支,运行记录逐节点落盘  │
│    subagent*.py  隔离上下文委派,受限批式,继承工具面   │
│    skills/       22 个攻防技能(勾选注入为系统通道)    │
├────────────────────────────────────────────────────────┤
│  toolbox/  工具服务器(经 MCP stdio 挂进 gateway)      │
│    tools/        53 个工具,目录即工具,增删热加载      │
│    mcp_bridge.py 外部 MCP 源桥接(jadx/x64dbg/ctf2),  │
│                  多操作源捆绑成单卡,上下文不随工具数涨 │
│    rag/          战果知识库语料(索引需重建)           │
├────────────────────────────────────────────────────────┤
│  external/  捆绑的第三方运行时与逆向工具链(见下表)    │
└────────────────────────────────────────────────────────┘
```

## 快速开始(Windows x64)

1. 安装 Python 3.12+(含 `py` launcher)。
2. 联网运行 `setup.bat`:创建 `gateway/.venv` 并安装依赖
   (fastapi/uvicorn/mcp/fastmcp/z3/pefile/capstone…),随后自动调用
   `install_tools.py` 从**官方 Release** 下载第三方工具二进制
   (nuclei/katana/zap/frida/fscan 等 15 项,幂等可重跑)。
   国内网络建议先 `set AGG_GH_PROXY=https://<你的gh代理>/`;
   也可单独运行 `py install_tools.py [工具名…]`,`--list` 看清单,
   无法自动获取的(DIE/nmap/blutter 等)在结尾列出,从本仓库
   GitHub Release 附件或原转移包复制。
3. 运行 `start.bat`,打开 <http://127.0.0.1:8300>,
   在设置里填 API 地址、模型名和 API Key(OpenAI 兼容协议)。

可选:

- 资产/笔记服务:`external\node.exe external\All_WP\server\index.cjs`(:8787,库为空起步)
- 移动端工具:`gateway\.venv\Scripts\python.exe -m pip install -r gateway\requirements-mobile.txt`

## MCP 依赖矩阵

网关自身把 `toolbox/server.py` 作为 MCP 工具服务器拉起,这部分开箱即用。
外部源分两种接入:**mcp.json 桥接**(stdio/http)与**直连 HTTP 工具卡**:

| 源 | 接入 | 包内状态 | 还需要什么 |
|---|---|---|---|
| toolbox 核心 | `config.yaml` tools 段 → stdio | ✅ 完整 | `setup.bat` 即可 |
| dnspy | 工具卡直连 :5150 | ✅ **全链路捆绑**(`external/dnspyEX` 含 MCP 扩展) | 无 |
| ctf2 | mcp.json → http 远程 | ✅(远程服务) | 环境变量 `CTF2_TOKEN` |
| jadx | mcp.json → stdio | ⚠️ MCP server 在,Python 依赖已覆盖 | **jadx-gui + 对应插件**(:8650),包内未捆绑 |
| x64dbg | mcp.json → stdio | ⚠️ dist 在,`node_modules` 有意排除 | 先 `npm install`;**x64dbg/x32dbg + 27042 插件**自备 |
| ida | 工具卡直连 :13337 | ⚠️ 仅调用端 | **IDA Pro + ida-pro-mcp 插件**自备(商业软件不可再分发) |

非 MCP 的可选集成:MuMu 模拟器(`AGENT_MUMU_MANAGER`,走 adb)、
FNID/POPCHAIN(`FNID_HOME`/`POPCHAIN_HOME` 运行时已保留)、
Visual Studio C++ / Git(Flutter 构建用)。均不附带凭据。

## 工具与技能

- **工具**(`toolbox/tools/`,每个子目录一张 `tool.yaml`):Web 打点/漏扫
  (ffuf/nuclei/sqlmap/zap/…)、子域与测绘(subfinder/oneforall/katana/…)、
  逆向四件套(ida/jadx/dnspy/x64dbg 分工卡)、脱壳与移动端(upx/pe_unpack/
  blackdex/flutter_blutter/unity_il2cpp/unidbg_emu)、文件与通用
  (file_edit/run_cmd/decode/hash_calc/…)。网页「工具 → 工坊」可可视化建卡。
- **技能**(`gateway/skills/`):从踩点、Web/云/无线/硬件攻击面到后渗透、
  红队 OPSEC、零日挖掘方法论的 22 套作战知识,对话时勾选生效。
- **RAG**(`toolbox/rag/`):Des-CTF 知识库 + 实战 combat-kb 语料;
  转移分发不带索引,首次使用需重建。

## 项目现状(2026-10)

**可用**:agent 循环(流式输出、任务中插话、上下文持久化与失败回滚)、
meta/native 双工具面、工作流 DAG(并行/迭代/循环/on_error 三分支)、
subagent 委派、RAG 检索、工具热加载、技能注入、前端全功能界面(会话/工作流画布/
知识库/工具坊)。本仓库是**干净源码分发**:会话、密钥、个人提示词、venv、
node_modules、构建缓存均已清除,不含任何凭据。

**待办**:

- [x] 补 `LICENSE`(MIT,Copyright Laomo456)
- [x] 根 `.gitignore`(venv / 运行时数据 / `external/` / *.zip / 构建产物)
- [x] 社区文档:CONTRIBUTING(含 Conventional Commits 提交规范)、
      CHANGELOG、SECURITY、.editorconfig
- [x] 第三方二进制与语料出库:`install_tools.py` 一键从官方渠道还原
      (仓库体积 1.7 GB → **1.24 MiB**,299 个源码文件)
- [ ] `requirements.txt` 锁版本;jadx-gui 与 x64dbg 插件的获取说明细化
- [ ] 测试目前仅有前端 smoke 脚本,后端无自动化测试
- [ ] `external/`(约 1.2 GB:JDK、dotnet、cmake/ninja、node、dnSpyEX、
      maven-repo 等)与 dalfox/DIE/nmap/blutter 等自构建产物不进 git,
      完整转移包以 Release 附件形式提供

## 安全与合规声明

本平台仅用于**已授权的渗透测试、CTF 竞赛与防御性安全研究**。
使用者须遵守目标系统所有者的授权与当地法律;因滥用产生的责任与本项目无关。
漏洞类工具(nuclei/sqlmap/fscan 等)在无授权目标上的使用是明确禁止的。

## 已知限制

- Windows x64 专用(路径、批处理、捆绑运行时均按 Windows 设计)
- 前端为预构建产物;改前端需自行 `npm i && npm run build`(源码在
  `gateway/frontend/src`)
- 单机单用户设计,无多租户/鉴权,不要暴露到公网
- 工作流/会话数据落盘在各自目录,迁移时直接拷贝 `workflows/`、`sessions/`
