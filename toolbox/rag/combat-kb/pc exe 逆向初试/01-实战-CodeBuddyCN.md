# 实战: CodeBuddy CN 逆向记录

> 腾讯 AI IDE, VS Code 魔改版。安装器: Inno Setup 6.7.0, Electron 壳 200MB
> 安装目录: `D:\CodeBuddy CN\`

## 1. 安装器 (CodeBuddy-win32-x64-user-4.12.0...exe)

| 项目 | 值 |
|---|---|
| PE 壳 | 1.4MB, x86_64, 5 DLLs (kernel32/user32/advapi32/comctl32/oleaut32) |
| Overlay | 155MB, 魔数 `zlb` 自定义容器 + LZMA/BZip2 压缩 |
| 特征字符串 | `file+0x9b391d0`: `Inno Setup Setup Data (6.7.0)` |
| 反调试 | 检测 `CrashReport/` + `code.lock` → kill(0) 自杀 |
| 绕过 | `--user-data-dir <空目录>` |

## 2. inno_updater.exe (Rust)

| 项目 | 值 |
|---|---|
| 语言 | Rust (MSVC), v0.16.0 |
| 源码 | `src/main.rs` |
| CLI | `inno_updater.exe <code_path> <silent:true\|false>` |
| 逻辑 | 解析 unins000.dat → move_update(old→new) → 处理 bin/manifest → 回写 |
| 错误类型 | `HeaderParseError/WriteError`, `FileRecParseError/WriteError`, `SystemTimeError` |

## 3. TDID 设备指纹 (electron.napi.node)

| 项目 | 值 |
|---|---|
| 身份 | 腾讯 TDID (Tencent Device ID) |
| 大小/架构 | 1.5MB, x86_64 C++ NAPI |
| 导入 | 28 DLLs / 352 函数 |
| 上报 | `POST https://tdid.m.qq.com/tmf` + `/event/report` |
| 加密链 | BCrypt(AES+SHA256) → JCE 序列化 → WinHTTP |
| 协议 | `token V3` + `secret`

**采集能力** — 11 类:

| 采集项 | API |
|---|---|
| 网卡MAC | `GetAdaptersAddresses` |
| GPU | `CreateDXGIFactory` |
| BIOS/UUID | `GetSystemFirmwareTable` |
| 显示器 | `EnumDisplayDevicesW` |
| 键盘布局 | `GetKeyboardLayoutList` |
| 鼠标 | `GetCursorPos` |
| 进程列表 | `CreateToolhelp32Snapshot` + `K32EnumProcessModules` |
| 硬件设备 | `SetupDiEnumDeviceInterfaces` |
| 注册表 | `RegOpenKeyExW` + `RegEnumKeyExW` |
| 跨进程 | `OpenProcess` + `ReadProcessMemory` |
| 鼠标钩子 | `SetWindowsHookExW` |

## 4. QIMEI (qimei.dll)

腾讯设备指纹老版本。1.2MB, x86_64, 12 DLLs/259 函数。`CryptGenRandom` + `InternetOpenA`。特殊节: `.gxfg` `.retplne` `.WinQ32` `.00cfg`

## 5. 其他 .node 模块

| 模块 | 大小 | 功能 |
|---|---|---|
| msalruntime.dll | 2.9MB | 微软 MSAL 认证 |
| vscode-sqlite3.node | 1.8MB | SQLite |
| spdlog.node | 558KB | 日志 |
| windows-registry.node | 127KB | 注册表 |
| kerberos.node | 204KB | Kerberos 认证 |

## 6. 动态验证

| 步骤 | 结果 |
|---|---|
| frida spawn | ❌ 反调试自杀 |
| `--user-data-dir` 绕过 | ✅ 启动成功 |
| frida attach + WinHTTP hooks | ✅ 钩子装好，需用户交互触发 TDID |