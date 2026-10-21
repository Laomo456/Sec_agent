# Frida 使用手册（nosuke 魔改版 · MuMu 环境）

> 来源：整理自 `D:\汇总\frida\`（README / Frida资源梳理.md / bin 产物）
> 环境：MuMu Player（Android 15，x86_64 + houdini ARM 转译）+ KernelSU root
> 版本：frida 17.11.0（PC 客户端与 server 版本匹配）

---

## 一、★ 一句话结论

**唯一可用的 server = `/data/local/tmp/nsv11`（nosuke 魔改版，md5 `f614ab1a`），默认监听 `127.0.0.1:31337`（不是 27042！）。**

---

## 二、快速上手（3 步）

```powershell
$ADB = "D:\MuMuPlayer\nx_main\adb.exe"

# 1. 推 server + 赋可执行权限（缺 chmod → Permission denied）
& $ADB -s 127.0.0.1:7555 push D:\汇总\frida\bin\ns-v11-raw /data/local/tmp/nsv11
& $ADB -s 127.0.0.1:7555 shell su -c "chmod 755 /data/local/tmp/nsv11"

# 2. 以 root 后台启动（用 run_background；不要 nohup &，会被本地解析器拆）
& $ADB -s 127.0.0.1:7555 shell su -c /data/local/tmp/nsv11

# 3. 转发 + 连接（端口是 31337）
& $ADB -s 127.0.0.1:7555 forward tcp:31337 tcp:31337
frida-ps -H 127.0.0.1:31337
```

> 模拟器连接：`adb connect 127.0.0.1:7555`

---

## 三、★ 连接铁律（血泪教训）

1. **attach 前重启 server** —— 连一次就断 / `connection closed` / `unexpectedly timed out` 时：
   ```
   killall nsv11 → 重启 → 等 3s → 再 attach
   ```
   （长时间运行/上次残留状态会导致后续连接不稳）

2. **server 必须以 root 起** —— 否则 attach 任何进程都 `unable` 且很快断。
   - 检查：`ps -A -o PID,USER,NAME | grep -i nsv11` 应显示 `root`
   - ⚠️ `adb shell su -c "killall x; nohup server &"` —— 远端串里的 `;` `&` `>` 会被**本地**解析器拆，`nohup` 落回 shell 身份 → 用 `run_background` 起 `adb shell su -c <server>`

3. **必须确认端口** —— nsv11 监听 **31337**。
   - 怎么查：`ls -l /proc/<pid>/fd | grep socket` 拿 inode → `grep <ino> /proc/net/tcp`
   - `0x7A69 = 31337`

4. **用 PID 不用名字** —— `frida -H 127.0.0.1:31337 -p <pid>`（名字枚举多进程 App 常失败，只列出 `:pushcore` 子进程之类）

5. **前台状态** —— 目标在后台被冻结时 attach/hook 无输出，先切前台。

---

## 四、frida 17 / 魔改版 API 差异（踩坑）

| 旧写法（16.x） | 新写法（17.x / nosuke） | 说明 |
|---|---|---|
| `Module.findExportByName('libc.so','openat')` | `Process.getModuleByName('libc.so').findExportByName('openat')` | 静态方法已移除 |
| `Module.findExportByName(null,'openat')` | `Module.findGlobalExportByName('openat')` | 全局兜底 |
| `Module.enumerateExports(name)` | `module.enumerateExports()` | 用实例方法 |
| `Process.findModuleByName` | 不变 | ✅ |

**Python 端注意**：frida 17 的 Python `create_script` **无 Java 桥**（`Java is not defined`）。
→ **Java 层 hook 必须用 CLI**：`frida -H .. -p .. -l x.js`

---

## 五、常用脚本模板

### 1. 最小 attach 验证
```bash
frida -H 127.0.0.1:31337 -p <pid> -e "console.log('OK '+Process.enumerateModules().length)" -q -t 10
```

### 2. 枚举自有模块（排除系统库）
```js
Process.enumerateModules().forEach(function (m) {
    if (!/^\/system|^\/apex|^\/vendor|^\/data\/dalvik/.test(m.path))
        console.log("APP-MOD " + m.name + " " + m.base + " " + m.path);
});
```

### 3. ★ 调用栈归因（定位行为来自哪个 so）
```js
function whoami(addr) {
    var m = Process.findModuleByAddress(addr);
    return m ? (m.name + "+0x" + addr.sub(m.base).toString(16)) : "?";
}
var openat = Process.getModuleByName("libc.so").findExportByName("openat");
Interceptor.attach(openat, {
    onEnter: function (a) { try { this.p = a[1].readCString(); } catch (e) {} },
    onLeave: function (r) {
        if (this.p && /maps|status|cmdline/.test(this.p)) {
            console.log("[open] " + this.p);
            Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 6)
                  .forEach(function (a) { console.log("   <- " + whoami(a)); });
        }
    }
});
```

### 4. 敏感路径观察
```js
["openat","open","access","faccessat","fopen"].forEach(function (fn) {
    var f = Process.getModuleByName("libc.so").findExportByName(fn);
    if (!f) return;
    Interceptor.attach(f, {
        onEnter: function (args) {
            try { var s = args[1].readCString(); } catch (e) { return; }
            if (s && /(\/su\b|magisk|frida|xposed|\/proc\/self\/maps|\/proc\/self\/status)/i.test(s))
                console.log("[" + fn + "] " + s);
        }
    });
});
```

### 5. 常驻 hook（CLI 退出后仍在）
```bash
frida -H 127.0.0.1:31337 -p <pid> -l script.js --eternalize -q -t 30
```

### 6. 回调防爆（必须）
```js
// 所有内存读 try-catch + 打印 cap（异常刷屏=双触发检测）
function ss(p){ try { return p.isNull()?null:p.readCString(); } catch(e){ return null; } }
var c = 0;
if (c++ < 100) console.log(...);
```

---

## 六、转译层注意（MuMu houdini）

- 纯 **ARM so 在转译下（0x4000 区）不在 frida 模块表** → `findModuleByName` 返回 null，hook 原始页 0 触发（跑的是 houdini 翻译缓存）
- 变通：hook 它**调用的 x86_64 边界库**（libc 的 write/connect/openat 等）
- **x86_64 库（libc/libssl/系统库导出）最稳**，必触发
- **别用 spawn（-f）**：houdini 不兼容（`solist_get_head` 不可执行内存）→ 崩。**一律 attach**（先正常启动，再挂）

---

## 七、目录说明（D:\汇总\frida\）

| 路径 | 内容 |
|---|---|
| `bin/` | 二进制：`ns-v11-raw`(197MB，**最佳**) / `frida-server-official`(官方对照) / `ns-old-device`(旧版备份) |
| `scripts/` | 编译/验证脚本（apply_patches / check_elf_hash / verify2 …）+ `anti_detect.js` / `probe_system.js` |
| `src/` | frida 源码（**勿动**，3.8GB） |
| `nosuke-full.patch` | 主魔改补丁（已手动修 reject） |
| `strongr-patches/` | strongR 反检测补丁（0001~0008） |
| `frida面具模块-新/` `frida模块/` | 面具模块（备用方案） |
| `work/` | 编译中间物 |
| `废弃/` | 旧版（`nosuke-server-旧-fd131795*`，勿用） |

### 产物对比（bin/）

| 文件 | 大小 | 说明 |
|---|---|---|
| `ns-v11-raw` | 197MB | **当前最佳**：源码编译 + GNU hash + 正确 helper.dex |
| `frida-server-official` | — | 官方原版，能连，火影 attach 会崩 |
| `ns-old-device` | 110MB | 设备拉回的旧版，全功能，防丢 |

---

## 八、★ 编译注意（编译机 Linux，root@192.168.253.135）

```bash
export PATH=/root/frida/frida/deps/toolchain-linux-x86_64/bin:/usr/local/bin:/usr/bin:/bin:$PATH
cd /root/frida/frida
meson configure build -Dstrip=false          # 关键：关 strip（否则产物无 hash）
ninja -C build subprojects/frida-core/server/nosuke-server
# 产物: build/subprojects/frida-core/server/nosuke-server-raw
python3 scripts/verify2.sh                   # 验证 hash
```

### 三层坑
1. **用 `-raw`，不要用 strip 过的 `nosuke-server`** —— `llvm-strip`/`elf-cleaner` 剥光 ELF hash → Android 12+ linker 拒绝加载（`dlopen failed: empty/missing DT_HASH/DT_GNU_HASH`）
2. **查 DT_GNU_HASH(0x6ffffef5)，不是只查 DT_HASH** —— 否则误判"无 hash"
3. **helper.dex 类名保持 `re/frida/HelperBackend`** —— 与 Android 类名无关，改了直接 `assert` Abort

---

## 九、魔改点（反检测特征替换）

| 检测点 | 原版 | 魔改后 |
|---|---|---|
| 默认端口 | 27042 | **31337**（集群 31347 / Inspector 19527） |
| 进程名 | frida-server | nosuke-server |
| Agent 库名 | frida-agent.so | nosuke-agent-<arch>.so（memfd） |
| 线程名 | frida-* / gum-* | nosuke-* |
| Socket 路径 | /frida-zymbiote- | /nosuk-zymbiote- |
| RPC 消息 | frida:rpc | Base64 编码 |
| D-Bus 数据目录 | re.frida.server | UUID 随机 |

---

## 十、快速排错

| 症状 | 原因 | 解决 |
|---|---|---|
| `connection closed` / `unexpectedly timed out` | server 状态残留 / 非 root 起 | killall → 重启 → 再连 |
| `can't execute: Permission denied` | 无 x 位 | `chmod 755` |
| 连不上（端口错） | 用了 27042/8080 | forward **31337→31337** |
| `Java is not defined`（Python） | Python 无 Java 桥 | 用 CLI |
| `Module.findExportByName is not a function` | frida 17 API | `Process.getModuleByName().findExportByName()` 或 `Module.findGlobalExportByName()` |
| spawn 崩 | houdini 不兼容 | 用 attach |
| attach 后脚本刷屏 | 回调没 try-catch | 全内存读包 safeStr + cap 打印 |
| 后台无输出 | 进程冻结 | 切前台 |

---

## Credits
- Frida: https://github.com/frida/frida
- strongR-frida: https://github.com/hzzheyang/strongR-frida-android
- 成果包：`D:\汇总\frida\`（nosuke-server，火影 60s+ 稳定）
