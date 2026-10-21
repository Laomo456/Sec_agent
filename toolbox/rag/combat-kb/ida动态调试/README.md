# IDA 动态调试 — MCP 插件 + 调试器模式

> 环境：IDA Pro 9.4.260714 + ida_mcp.py 插件（D:\IDA Pro 9.4.260714\plugins\）

---

## 一、IDA MCP 插件（oolde/旧版）

### 安装位置
```
D:\IDA Pro 9.4.260714\plugins\ida_mcp.py   ← 入口
D:\IDA Pro 9.4.260714\plugins\ida_mcp\      ← 实现包
```

### 作用
- 在 IDA GUI 里启动一个 MCP 服务器（默认端口 13337）
- 让 AI（Claude/Cursor 等）能远程调用 IDA 的分析/反编译/调试 API
- 打开 IDA GUI 时会自动加载（autostart 默认 true）

### 与 headless idalib-mcp 的区别

| 方式 | 适用场景 | 缺点 |
| --- | --- | --- |
| **idalib-mcp**（推荐） | 批量/CLI/服务器端 | 不依赖 GUI，但分析深度有限 |
| **ida_mcp.py**（GUI 插件） | 交互式调试、看反汇编 | 需要开着 IDA GUI，占用资源 |

---

## 二、IDA GUI 调试器（Windows + GDB）

IDA GUI（ida.exe/ida64.exe）内置了调试器，可以连：
- 本地 Windows 进程
- 远程 Linux gdbserver（包括 Android SO）
- WinDbg

### 调试 Android SO 的流程
1. Android 设备/模拟器上跑 `gdbserver :23946 /path/binary`
2. 端口转发：`adb forward tcp:23946 tcp:23946`
3. IDA → Debugger → Attach → Remote GDB Debugger
4. 填 host: 127.0.0.1:23946

### 不需要 ADB 的替代方案
- 用 unidbg 本地模拟执行（见 `本地so分析与调试\方法(动态调试)\unidbg本地hook.md`）
- unidbg 不触发反调试，可反复跑

---

## 三、已知问题

- `idat.exe`（命令行模式）没有调试器，只有 GUI 版有
- IDA 9.x 的 IDAPython API 有大量变更（见 `ida自内cli功能配合(急速)`）
- 插件在 IDA 9.4 上已验证可加载