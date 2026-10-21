# 更新日志

本项目的显著变更记录在此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划

- `external/` 完整转移包以 GitHub Release 附件形式分发
- jadx-gui 与 x64dbg 插件的获取脚本/说明细化
- 后端自动化测试框架接入

## [0.1.0] - 2026-10-21

首个开源版本。功能基线:

### 新增

- **install_tools.py 一键安装器**:仓库不含第三方二进制
  (nuclei/katana/subfinder/interactsh/ffuf/fscan/upx/zap+JDK17/frida/
  adb/ysoserial/blackdex + Des-CTF 语料),从官方 Release 动态解析
  最新版下载还原,幂等、支持 `AGG_GH_PROXY` 代理;setup.bat 自动调用
- **agent 网关**(gateway):OpenAI 兼容模型接入、自研 agent 循环
  (流式输出、任务中插话、上下文持久化、连续失败回滚墓碑、防重复兜圈)、
  meta / native 双工具面
- **工作流引擎**:Dify 式可视化 DAG(Vue Flow 画布),并行分支、
  iteration / loop 子图、`on_error` 三分支,运行记录逐节点落盘
- **subagent 委派**:隔离上下文、受限批式、继承宿主工具面
- **工具服务器**(toolbox):53 张工具卡热加载(script / http / command
  三实现),覆盖 Web 打点、漏扫、子域测绘、逆向(ida / jadx / dnspy / x64dbg
  四卡分工)、脱壳与移动端(flutter_blutter / unity_il2cpp / unidbg_emu)、
  通用文件与命令执行
- **外部 MCP 桥接**:jadx / x64dbg / ctf2 三源接入;多操作源捆绑成单卡,
  上下文不随工具数增长;ida / dnspy 走直连 HTTP 工具卡
- **RAG 知识库**:Des-CTF 知识库与实战 combat-kb 语料,网页管理面
- **技能系统**:22 套攻防作战技能,对话勾选注入
- **前端**:Vue3 + Naive UI + Vue Flow,会话 / 工作流画布 / 知识库 / 工具坊
  全功能界面(预构建,无需 Node)

### 说明

- 本版本为**干净源码分发**:会话、密钥、个人提示词、venv、node_modules、
  构建缓存均已清除;第三方二进制/运行时/语料一律不入库
  (仓库体积 1.24 MiB),由 install_tools.py 或 Release 附件还原
- Windows x64 专用;单机单用户设计,无多租户与鉴权
