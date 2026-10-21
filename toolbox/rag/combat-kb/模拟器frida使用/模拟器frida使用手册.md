# 模拟器 frida 使用 — 完整手册

> 2026-08-15 更新 | 环境: MuMu 12 (Android 15 x86_64 + houdini ARM 转译) + 魔改 nosuke frida 17.11.0
> 反检测联动: 见 `D:\know base\模拟器崩溃以及反检测\模拟器游戏反检测突破-通用方法论.md`

---

## 一、部署 (nosuke 魔改版, 已配置好)

| 组件 | 位置 | 说明 |
|---|---|---|
| frida server | 设备 `/data/local/tmp/x/nsv11` | 197MB raw 版 (未 strip, GNU hash 保留) |
| 端口 | **31337** (非默认 27042) | nosuke 补丁修改 |
| PC 客户端 | `D:\原版frida\` (17.11.0 全套) | frida/frida-tools CLI |
| Python | 3.14.3 + frida 17.11.0 + frida-tools 14.9.0 | pip 环境 |
| 编译机 | root@192.168.253.135 `/root/frida/frida` | 源码 (改补丁重编译用) |

**启动 server**:
```bash
adb -s 127.0.0.1:7555 shell "su -c 'nohup /data/local/tmp/x/nsv11 -D >/dev/null 2>&1 &'"
```

**端口转发 (每次连设备都要)**:
```bash
adb -s 127.0.0.1:7555 forward tcp:31337 tcp:31337
```

---

## 二、★ 连接铁律 (血泪教训)

### 1. attach 前必重启 nsv11
```
失败症状: "Failed to attach: unexpectedly timed out while waiting for stop"
原因: 长时间运行/上次 attach 残留状态
解决: killall nsv11 → 重启 → 等 3s → 再 attach
```
```bash
adb shell "su -c 'killall nsv11; sleep 1; nohup /data/local/tmp/x/nsv11 -D >/dev/null 2>&1 &'"
adb shell "su -c 'ss -tlnp | grep 31337'"   # 确认 LISTEN
```

### 2. 用 PID 不用名字
```
frida -H 127.0.0.1:31337 -p <pid>   # 枚举名字失败时用 PID
```

### 3. 游戏前台状态检查 (后台冻结时 attach/hook 无意义)
```bash
adb shell "su -c 'dumpsys activity activities | grep topResumedActivity | head -1'"
# 期望: topResumedActivity=...com.tencent.KiHan/.MainActivity
# 后台: 24 线程冻结; 前台: 150+ 线程活跃
adb shell "su -c 'ls /proc/<pid>/task | wc -l'"   # 线程数佐证
```

---

## 三、★ 与反检测共存的战术 (时序窗口法)

**原理**: TP 周期性扫描代码完整性 (周期 15~30s)。hook 装上 12s 内取完数据立即 detach = 检测不到。

**执行模板**:
```
1. 重启 nsv11 (铁律 1)
2. 确认游戏前台 (铁律 3)
3. attach + 装 hook (只 hook 目标, 最小化)
4. 立即触发活动 (input tap/swipe) — 窗口内无活动=无数据
5. ≤12s 取完数据 (打印 cap 条数, fd 过滤)
6. 立即 detach (Ctrl-C / 脚本退出)
7. 检查进程存活 (ps | grep)
8. 重试间隔 30s+ (错开校验周期)
```

**安全分级 (实测)**:
| 操作 | 安全性 |
|---|---|
| attach + 读内存 + 模块枚举 | ✅ 随便用 |
| hook ≤12s 后 detach | ✅ 安全 |
| hook 30s+ | ❌ 死 |
| 改 libc 入口 (openat/stat) | ❌ 死 (TP 校验) |
| spawn 模式 (-f 冷启动) | ❌ 崩 (houdini 不兼容) |

**hook 回调防爆 (必须)**:
```js
// 1. 所有内存读 try-catch (异常刷屏=双触发检测)
function safeStr(p) { try { return p.isNull() ? null : p.readCString(); } catch (e) { return null; } }
// 2. 打印 cap + fd 过滤 (read 高频函数数据爆炸)
var c = 0;
if (c++ < 100 && this.fd === 150) console.log(...);
```

---

## 四、frida 17 API 变化 (踩坑记录)

| 旧写法 (16.x) | 新写法 (17.x) | 说明 |
|---|---|---|
| `Module.findExportByName('libc.so','openat')` | `Module.findGlobalExportByName('openat')` | 静态方法已移除 |
| `Module.enumerateExports(module.name)` | `module.enumerateExports()` | 用模块实例方法 |
| `Process.findModuleByName` | 不变 | ✅ |

---

## 五、常用脚本模板

### 1. 最小 attach 验证
```bash
frida -H 127.0.0.1:31337 -p <pid> -e "console.log('OK '+Process.enumerateModules().length+'m');"
```

### 2. hook openat 看路径 (12s 窗口)
```js
var openat = Module.findGlobalExportByName('openat');
Interceptor.attach(openat, {
    onEnter: function (args) { this.p = safeStr(args[1]); },
    onLeave: function (r) { if (this.p) console.log('openat(' + this.p + ')=' + r); }
});
```

### 3. 敏感路径观察器 (含栈回溯)
```js
var sensitive = ['/proc/self/maps','/proc/self/status','/sys/kernel','/proc/modules'];
['openat','openat64','open','stat','access','faccessat'].forEach(function (fn) {
    var f = Module.findGlobalExportByName(fn);
    if (!f) return;
    Interceptor.attach(f, {
        onEnter: function (args) { this.p = safeStr(args[1] || args[0]); },
        onLeave: function (r) {
            if (hits > 300) return;
            if (this.p && sensitive.some(s => this.p.indexOf(s) === 0)) {
                hits++;
                var bt = Thread.backtrace(this.context, Backtracer.FUZZY);
                console.log('[SENS] ' + fn + '(' + this.p + ') = ' + r);
                bt.slice(0, 10).forEach(a => console.log('   ' + a));
            }
        }
    });
});
```

### 4. fd 过滤观察 (找检测循环)
```js
// 先 ls -l /proc/<pid>/fd/ + readlink 找可疑 fd, 再精确过滤
var read = Module.findGlobalExportByName('read');
Interceptor.attach(read, {
    onEnter: function (a) { this.fd = a[0].toInt32(); },
    onLeave: function (r) { if (this.fd === 150) console.log('cpuinfo read=' + r); }
});
```

### 5. 模块/导出枚举
```js
var h = Process.findModuleByName('libtersafe.so');   // ARM so 在转译下也能枚举
h.enumerateExports().forEach(e => console.log(e.type + ' ' + e.name + ' @ ' + e.address));
```

---

## 六、故障排除速查

| 症状 | 原因 | 解决 |
|---|---|---|
| timed out while waiting for stop | nsv11 状态残留 | 重启 nsv11 |
| unable to connect to remote frida-server | 忘 forward | `adb forward tcp:31337 tcp:31337` |
| Process terminated | 游戏被 TP 杀 | 时序窗口超时/hook 面太大 → 缩短窗口 |
| Module.findExportByName is not a function | frida 17 API | 用 findGlobalExportByName |
| attach 成功但脚本报错刷屏 | 回调没 try-catch | safeStr 包住所有内存读 |
| 游戏后台冻结 hook 无输出 | Freezer | 切前台 (monkey LAUNCHER) 再试 |
| spawn (-f) 崩溃 | houdini 不兼容 | 用 attach 模式 (游戏先手动启动) |
| 游戏闪退无 tombstone | 被 kill | 查 `logcat -d -b events \| grep am_kill` |

---

## 七、火影专用速查 (案例)

```
进程: com.tencent.KiHan  (grep KiHan | grep -v xg_vip)
前台检查: dumpsys activity activities | grep topResumedActivity
TP 家族: libtersafe/libtprt/libtgpa/libcgsdk (全 ARM 转译, frida 可枚举但 hook 会被校验)
安全窗口: 12s (15s 边界, 30s 必死)
cpuinfo 伪造: 见 反检测方法论 §四 + 附录 A6
fd 150/154 = /system/lib64/arm64/cpuinfo (环境检测循环)
```

---

## 八、★ 内存大杀器：dd if=/proc/PID/mem（2026-09-14 新增）

### 8.1 为什么需要 dd

frida 的 `readByteArray` 在 Android 15 上经常返回 `Permission denied`：
- 原因：内核 `process_vm_readv` 被 SELinux 策略拦截（即使是 root）
- `Memory.scanSync` 能定位地址，但读不了内容——看得到拿不到

**解决方案**：`setenforce 0` 后用 `dd if=/proc/PID/mem` 直接读。

### 8.2 基本命令

```bash
# 1. 关 SELinux（必须）
su -c 'setenforce 0; getenforce'  # → Permissive

# 2. dd 读内存
#    skip = 虚拟地址 // bs
#    count = 要读的字节数 // bs
su -c 'PID=$(pidof com.xxx | cut -d" " -f1); \
  dd if=/proc/$PID/mem of=/data/local/tmp/out.bin \
  bs=4096 skip=<addr//4096> count=<size//4096>'

# 3. 验证
head -c 8 /data/local/tmp/out.bin | xxd
```

### 8.3 ⚠️ 血泪坑：mksh $(( )) 是 32 位

```bash
# 错误——mksh 截断高 32 位
dd skip=$((0x7bdb71558000/4096))    # 截成 0x71558000，读错地方

# 正确——Python 算
# python -c "print(0x7bdb71558000//4096)"
dd skip=33247663448 count=3663
```

**永远不要**在 adb shell 里用 `$((0x超大地址/4096))`。Python 算好十进制传进去。

### 8.4 dd vs frida readByteArray 性能对比

| 方法 | 速度 | 权限要求 | 适用范围 |
|---|---|---|---|
| `readByteArray(1MB)` | ~50ms | SELinux + process_vm | 小段读取 |
| `dd bs=4096 count=256` | ~1ms | SELinux permissive | 大段 dump |

实战经验：**先用 frida 定位地址，再用 dd 拉数据**。

### 8.5 典型联用场景

```
frida 定位         →  dd 拉取            →  jadx 分析
Memory.scanSync     dd if=/proc/PID/mem    jadx-gui classes.dex
  找 dex magic         按地址 dump
  扫 classes>500       直接裸读             逆向业务代码
```

### 8.6 frida 找不到 ARM SO（Houdini 转译）

```bash
# frida 找不到——但 maps 里有
grep libxxx /proc/PID/maps
# 400020e00000-400020e03000 ... libdexjni.so

# ARM SO 地址特征：0x4000xxxx（独立 ARM64 用户态空间）
# frida findModuleByName 只认 x86_64 模块表
```

**解决方案**：用 maps 地址 + `ptr(addr)` 直接操作：

```javascript
var base = ptr('0x400020e13000');  // maps 里读到的地址
var head = base.readByteArray(64); // 直接读
```

### 8.7 am start -D 不冻结 native（debug 模式陷阱）

```bash
am start -D com.pkg/.Activity
```

`-D` 只冻结 Java 层等待 jdb 连接。**native init（含壳解密）照常执行**。
所以 hook art::DexFile::OpenMemory 方案不可靠——等你 attach 上去，壳早跑完了。

**替代方案**：提前 attach（进程启动 200ms 内）+ 等壳解密完再扫内存。

---

## 九、升级策略

- **当前结论: 不用升级官方版** — nosuke 17.11.0 反检测特征已知, 战术层 (时序窗口) 与版本无关
- 遇新游戏针对性检测 → 编译机重放补丁流程 (strip=false + elf-cleaner:off 保 GNU hash, 见 `Desktop\火影\进度总结.md`)
- 官方新版本改动大, 魔改重打补丁成本高; 除非需要新 API 功能否则不动

---

## 十、★★ Java bridge 无 → 纯 JNI 直调 (2026 实战: 快手 sig3)

> 详细实现见 `D:\know base\Java bridge\README.md` 方案 B 实战。本节只记**设备侧踩坑**。

### 10.1 两台 server 都无 Java bridge
`nsv11` 与设备里的 `frida-server-official` **实测都** `ReferenceError: Java is not defined`（Android12+ 删了 `registerFrameworkNatives`）。
⇒ **`Java.perform` 完全不可用**。别再把时间耗在 Java hook 上，直接上**纯 JNI**（`JNI_GetCreatedJavaVMs`→`AttachCurrentThread`→app ClassLoader `loadClass`→调方法）。

### 10.2 server 必须以 **root** 起，否则 attach **所有**进程都 `unable to access process with pid`
先确认身份：
```bash
adb -s <host:port> shell su -c "ps -A -o PID,USER,NAME" | findstr /I <server>
# 显示 shell = 没提权；显示 root = OK
```
**✗ 坑**：`adb shell su -c "killall x; nohup /path/server -D >/dev/null 2>&1 &"` —— 远端串里的
`;` `>` `2>&1` `&` `|` 会被**本地命令解析器**拆掉，`su` 只作用于**第一段**，`nohup server &` 落回 `shell` 身份。
**✓ 正解**：本地 `run_background` 起 `adb shell su 0 <server>`（保活、无重定向）：
```bash
adb -s <host:port> shell su 0 /data/local/tmp/frida-server-official
adb -s <host:port> forward tcp:27042 tcp:27042      # 官方默认 27042
```

### 10.3 卡死的脚本会残留 → 下次 `create_script` 超时
现象：上一次脚本卡在 JNI 调用里 → 重跑时 `frida.TransportError: timeout was reached`(create_script/load)。
解法：`adb shell su -c "am force-stop <pkg>"` **重启 app** 清线程态；必要时再重启 server。

### 10.4 远端/本地 `|` 别混
- `adb shell su -c "cat /proc/PID/maps" | findstr /C:"/lib/"` —— 这个 `|` 是**本地**的，OK。
- `adb shell su -c "grep a|b /x"` —— 这个 `|` 在**远端串**里，会被本地解析器拆 → 报 `a: inaccessible`。**远端别用 `|`**。

### 10.5 ARM so 不在 frida 模块表（转译层）
`Process.findModuleByName('libkwsgmain.so')` → null（frida 只认 x86_64 模块表）。
地址走 `/proc/<pid>/maps`（`0x4000xxxx` 区）。但**若只走纯 JNI 调 Java 层, 完全不碰 ARM so**（本次就是这么破的）。

### 10.6 方法论: 动静结合
本次 sig3 = **静态定位 + 动态执行**缺一不可：
- **静**：单独 jadx 反编译目标 `classesN.dex`（大 APK 的 jadx 输出常漏某些 dex）→ 命中组装点，拿到**确切 API / 常量 / 入参构造**。
- **动**：设备端纯 JNI 直调该 API → **运行时值 + 真值 + 性质(确定性/随机)**。
- 只动 → 不知道调谁(瞎猜参数全 null)；只静 → 造不出真值/证不了。**对上就通**。

---

## 十一、★ frida hook .so 的"最稳法" (2026-09-30 实测, MuMu/houdini 快手)

**一句话**: **只信 x86_64 边界库, 别指望 hook ARM 转译库**; 需 Java 层就用 frida **CLI**。

### 稳定性排序 (实测)
| 目标 | 稳不稳 | 说明 |
|---|---|---|
| **x86_64 libc/libssl/系统库导出** | ✅ **最稳** | `Interceptor.attach` 必触发 (实测 libc `write` 稳触发 + **app 存活**) |
| app 自带 x86_64 so | ✅ 稳 | 在模块表内 |
| **ARM 转译库 (0x4000 区)** | ❌ **无效** | 不在模块表; hook 原始页 **0 触发**(跑的是 Houdini 翻译缓存) |
| 观察 ARM 库"起了什么作用" | ✅ 变通 | hook 它**调用的 x86_64 libc**(ARM→x86 libc): `write/connect/openat` |

### 最稳模板 6 条 (脚本 `C:\Users\20751\kwai_work\frida_so_stable.js`)
1. **attach, 绝不 spawn** (spawn → libhoudini `solist_get_head` 不可执行内存 → 崩)
2. **魔改 frida**(nosuke); attach 前必要时重启 server (见 §二铁律)
3. **Frida17 取导出**: 先 `Process.getModuleByName(m).getExportByName(f)`, 兜底 `Module.findGlobalExportByName(f)`
4. **回调全程 try-catch + 打印 cap** (异常刷屏 = 双触发检测)
5. **只 hook 目标 + ≤12s 窗口 detach** (反检测 App)
6. 需 **Java 层**(如调签名接口) → 用 **CLI**(`frida -H .. -l x.js`); Python `create_script` **无 Java 桥**

### 实测 (快手 com.smile.gifmaker)
```
x86 modules=352   ARM libs(maps /arm64/)=109
hooked libc.so!write / !connect / !openat + libssl.so!SSL_write
write fd=506 len=8192 ×N        ← 稳触发
app survived: True              ← 存活
```
> 结论: **hook so 最稳 = 只挂 x86_64 边界库 + 6 条铁律**; ARM 库一律"从 x86 边界观察 / 上探 Java 层 / 仿真", 别硬 hook 原始页。
