# ATM Legend 逆向工程总档

> 最后更新: 2026-07-24
> 游戏: com.joym.atmlegend2.yofun.mumu (Unity IL2CPP + NetHTProtect)
> 模拟器: MuMu x86_64 Android 12
> Frida: nosuke Release (QuickJS, 96MB) @ :31337

---

## 一、工具链

```
本地:  D:\MuMuPlayer\nx_main\adb.exe
       D:\汇总\新frida\nosuke-release (96MB)
       D:\汇总\新frida\hook_recv_mem.js
       D:\汇总\新frida\arm_scan.py
VM:    192.168.253.135 (Frida 源码 /root/frida/frida/)
       SSH: root@192.168.253.135 (密钥认证)
模拟器: MuMuPlayer Android 12 (x86_64 + houdini)
       内部 nosuke: /data/local/tmp/.cache/sys-helper @ :31337
       Magisk 模块: frida_hidden_v3 (开机自启)
```

全部命令:
```bash
# 连接 ADB
D:\MuMuPlayer\nx_main\adb.exe connect 127.0.0.1:7555

# 转发端口
D:\MuMuPlayer\nx_main\adb.exe forward tcp:31337 tcp:31337

# 启动游戏
D:\MuMuPlayer\nx_main\adb.exe shell monkey -p com.joym.atmlegend2.yofun.mumu 1

# Attach 抓包
python -c "
import frida, time
d = frida.get_device_manager().add_remote_device('127.0.0.1:31337')
s = d.attach('奥特曼传奇英雄2')
with open('D:/汇总/新frida/hook_recv_mem.js') as f:
    script = s.create_script(f.read())
script.on('message', lambda m,d: print(m.get('payload',{})))
script.load()
input()
"

# 搜内存
python D:/汇总/新frida/arm_scan.py
```

---

## 二、网络协议

### 2.1 HTTPS

```
Hook:    SSL_write / SSL_read (libssl.so)
主机:    atmweb.joymeng.com
内容:    HTTP/2 JSON (登录/房间/匹配/战斗)
抓包脚本: live2.js / capture_log.js
URL 汇总:
    /api/v1/user/game/heartbeat?cv=3.2.42&...
    /login  /room/enter  /match/start  /battle/action
    /player/info?game_user_id=aebwuyojmabdshox
```

### 2.2 自定义 TCP 协议

```
Hook:    recv (libc.so)
特征:    魔数 "nysv" (6e 79 73 76)
端口:    动态下发
协议结构:
    偏移 0-3:  "nysv" 魔数
    偏移 4-7:  未知 (固定 00 00 00 01)
    偏移 8-11: int32 LE 游戏数值 ← 关键
    偏移 12+:  后续数据

已知数值:
    574720 → 游戏内 100 coin 对应值 (推测)
    257720 → 关联房主? (推测)
```

### 2.3 自定义 Headers

```
Player-UUID:  7fb2d6ba-cc40-4918-813e-84a57a2ceb93
Player-Channel: gw-overseas12
Player-Engine: NEMUX (MuMu 标识!)
X-Client-Sign: SHA256 签名 (HMAC?)
X-Client-Nonce: 时间戳 + 随机
User-Agent: Dalvik/2.1.0 (Linux; U; Android 12; MuMu)
```

---

## 三、ARM 内存

### 3.1 关键发现

```
Frida 可以用 ptr(addr).readByteArray(size) 读 ARM 内存
❌ Memory.readByteArray(addr, size) → "not a function" (已废弃的 API)
✅ ptr(0x031f0000).readByteArray(64) → ELF header (7f 45 4c 46)

ARM rw-p 区域: 273 个，大小不一 (最大 948KB 匿名堆)
```

### 3.2 常见基址 (重启后变化)

```
libunity.so (ARM):   0x031f0000 (r--p)
ARM 堆:              0x049fe000 附近 (948KB rw-p)
libc.so (ARM):       0x0ba84000
libhoudini.so (x86): 0x75c83fda4000
libandroid_runtime:  0x75caf6f91000
```

### 3.3 内存搜索方法

```python
# arm_scan.py 流程:
# 1. 读 /proc/pid/maps 获取 ARM rw-p 区域 (< 0x80000000)
# 2. 用 ptr(start).readByteArray(size) 逐块扫描
# 3. 匹配 int32 值或字节模式
# 4. 用 ptr(addr).writeByteArray([...]) 修改

# 示例: 搜索数值 574720
# target = struct.pack('<i', 574720)
# 在 ARM rw-p 区域逐块搜索
```

注意: IL2CPP + NetHTProtect 可能加密运行时数据。用户 ID 和数值在内存中搜不到明文。

---

## 四、环境检测绕过

### 4.1 nosuke 编译时 (frida_patch.sh)

| 检测项 | 绕过方式 | 原始值 → 伪装值 |
|--------|---------|----------------|
| 端口扫描 | 改监听端口 | 27042 → 31337 |
| 进程名 | 改 argv[0] | frida-server → sys-helper |
| 服务目录名 | 改文件路径 | re.frida.server → re.svc.media |
| Agent 文件名 | 改 so 名 | frida-agent → media-agent |
| 线程名 | 改线程名 | frida-* → media-* / nosuke-* |
| Helper 名 | 改二进制名 | frida-helper → media-svc |

### 4.2 运行时 (bypass.js Frida 脚本)

| 检测项 | 绕过方式 |
|--------|---------|
| /proc/self/maps 含 frida | fopen/fopen64 拦截过滤 |
| TracerPid | /proc/self/status 改 0 |
| /proc/net/tcp 31337 | 过滤 frida 相关行 |
| Magisk 路径 | openat 拦截 |
| su 命令 | execve 拦截 |

---

## 五、Frida 编译 (VM 192.168.253.135)

### 5.1 源码结构

```
/root/frida/frida/
├── subprojects/
│   ├── frida-core/          ← 核心 (server + agent + inject)
│   │   └── src/linux/
│   │       └── linux-host-session.vala  ← Java bridge 入口
│   ├── frida-gum/           ← 底层引擎 (Interceptor/Memory)
│   │   └── bindings/gumjs/  ← JS runtime (V8 or QuickJS)
│   ├── frida-python/        ← Python binding
│   └── frida-tools/         ← CLI (frida-ps, frida-trace)
├── releng/                   ← 构建工具
├── build/                    ← 编译目录
│   └── frida-android-x86_64.txt  ← 交叉编译配置
└── deps/                     ← 预编译依赖
```

### 5.2 编译命令

```bash
# Debug + QuickJS (稳定)
cd /root/frida/frida
python3 releng/meson/meson.py configure build/ \
    -Dbuildtype=release -Ddebug=false -Dstrip=true
cd build && ninja -j2

# Release + V8 + File API
python3 releng/meson/meson.py configure build/ \
    -Dfrida-gum:v8=enabled -Dfrida-gum:quickjs=disabled \
    -Dbuildtype=release -Ddebug=false -Dstrip=true
cd build && ninja -j2
```

### 5.3 补丁文件

```
/root/patch_java.py          → GetCreatedJavaVMs + delegate
/root/fix_java_full.py       → 跳过 create_java_vm (导致 agent 挂死)
/root/fix_delegate2.py       → delegate 修复
/root/fix_scope.py           → 变量作用域修复
```

---

## 六、Java Bridge 状态

```
Android 12+: registerFrameworkNatives 已删除 → Java hook 不可用
nosuke: GetCreatedJavaVMs 符号已读但不执行 (无害)
绕过: LSPosed / 直接 JNI / DexClassLoader 补丁

Unity IL2CPP 游戏: 不需要 Java bridge
```

---

## 七、下一步 & 待办

```
[ ] ARM 内存搜索找数值 → 直接修改
    - arm_scan.py 已准备好，开机就能跑
    - 用 recv 抓到的 nysv 值 (574720) 做参考搜索

[ ] recv 改包验证
    - mod_packet.js 已写好
    - 修改 nysv 偏移 8-11 的 int32 值

[ ] V8 + File API 版重编
    - VM 开机后编 release+v8
    - 需要消息大小补丁重打

[ ] NativeBridge getTrampoline 探索
    - 已验证 NativeBridgeItf 可访问
    - loadLibrary hook / isSupported 测试
```

---

## 八、已知问题

| 问题 | 原因 | 影响 |
|------|------|------|
| enumerate_processes NPE | PackageManager null | 不影响 attach/PID |
| 手动启动不能 attach | SELinux 上下文 | Magisk 自启可解决 |
| 用户 ID 搜不到明文 | NetHTProtect 加密 | 需模糊搜索 |
| 消息大小补丁被回退 | git checkout | 需重打 |
