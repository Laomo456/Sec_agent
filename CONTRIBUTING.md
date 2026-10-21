# 贡献指南

感谢关注 AGG。本文说明开发环境、代码约定与提交规范。

## 开发环境

- Windows x64 + Python 3.12+(含 `py` launcher)
- 首次:`setup.bat`(创建 `gateway/.venv` 并安装全部依赖)
- 启动:`start.bat` → <http://127.0.0.1:8300>
- 改前端:`gateway/frontend/` 下 `npm i && npm run dev`(Vite);
  发布前 `npm run build` 重新生成 `dist/`(dist 保持入库,保证克隆即用)
- 跑工具服务单测式验证:直接 `gateway\.venv\Scripts\python.exe toolbox\server.py`
  可脱离网关独立起工具面,便于调试工具卡

## 代码结构速查

| 目录 | 职责 |
|---|---|
| `gateway/` | FastAPI 网关、agent 循环、工作流引擎、subagent、技能 |
| `toolbox/` | 工具服务器(MCP)、53 张工具卡、RAG、外部 MCP 桥接 |
| `gateway/frontend/` | Vue3 + Vue Flow 前端 |
| `external/` | 第三方运行时(**不入库**,见 README 的分发说明) |

## 如何贡献

### 新增工具卡

每个 `toolbox/tools/<名字>/` 目录一张 `tool.yaml`,支持 script / http / command
三种实现,热加载无需重启。字段规范见 [`toolbox/tools/README.md`](toolbox/tools/README.md)。
要求:

- `danger` 必须如实标(readonly / intrusive / destructive)
- 多操作工具用 `op_danger` 逐操作标注
- `example` 给最小可用示例,模型照抄最不容易错

### 新增技能

`gateway/skills/<名字>/` 放 Markdown 作战知识;对话时勾选注入。内容要求
可复现、可验证,避免空话。

### 提交规范(Conventional Commits)

```
<type>(<scope>): <subject>

[body]
[footer]
```

- **type**:`feat` 新功能 / `fix` 修复 / `docs` 文档 / `refactor` 重构 /
  `perf` 性能 / `test` 测试 / `chore` 杂务 / `release` 发版
- **scope**(可选):`gateway` / `toolbox` / `frontend` / `tools` / `skills` / `docs`
- **subject**:中文或英文均可,祈使语气、不加句号,≤ 50 字

示例:

```
feat(tools): 新增 hashcat 工具卡,支持字典与规则模式
fix(gateway): 修复 WS 断连后事件回放重复的问题
docs: 补充 MCP 依赖矩阵
```

### 分支与 PR

- `main` 保持可用;开发用 `feat/<名字>` 或 `fix/<名字>` 分支
- PR 描述:改了什么、为什么、怎么验证的(附运行截图或日志片段)
- 涉及工具卡的改动,请在 PR 里给出一次真实调用输出

## 安全红线

- **不要提交任何真实凭据**:`config.yaml` 里的 `api_key` 保持空串;
  本地调试填了 key 就不要 `git add gateway/config.yaml`
- **不要提交目标数据**:会话/扫描输出/凭据样本一律不进库
  (`.gitignore` 已排除 `gateway/sessions/` 等运行时目录,新增落盘路径记得同步)
- 工具类贡献必须声明用途边界:仅授权测试场景
