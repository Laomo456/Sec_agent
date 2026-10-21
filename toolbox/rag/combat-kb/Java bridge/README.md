# Java bridge — Frida 的 Java Hook 机制

> Frida 版本: 17.11.0 | 最后更新: 2026-07-24

---

## 一、什么是 Java bridge

Frida 的 Java bridge 允许在 JavaScript 中对 Android 应用的 Java 层进行 Hook，是 Frida 最核心的能力之一。

```javascript
Java.perform(function() {
    var OkHttp = Java.use("okhttp3.OkHttpClient");
    OkHttp.newCall.implementation = function(request) {
        console.log("[OkHttp] " + request.url().toString());
        return this.newCall(request);
    };
});
```

## 二、架构与原理

```
┌─────────────────────────────────────────────────────────────┐
│  Frida JS (agent)                                           │
│  Java.perform / Java.use / Java.choose                      │
└──────────┬──────────────────────────────────────────────────┘
           │ JS ↔ Native bridge
┌──────────▼──────────────────────────────────────────────────┐
│  frida-core / linux-host-session.vala                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ 1. 加载 libart.so                                    │    │
│  │ 2. 创建/获取 Java VM (JNI_CreateJavaVM)              │    │
│  │ 3. 调用 registerFrameworkNatives                     │    │
│  │ 4. 创建 HelperBackend 实例                           │    │
│  │ 5. JS ↔ Java 通过 JNI 通信                           │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 三、支持的 Android 版本

| Android 版本 | JNI_CreateJavaVM | registerFrameworkNatives | Java bridge |
|-------------|------------------|------------------------|-------------|
| ≤ 11        | ✅ 可用           | ✅ 可用                 | ✅ 正常     |
| 12          | ❌ 已有 VM       | ❌ 已删除               | ❌ 不可用   |
| 13+         | ❌ 已有 VM       | ❌ 已删除               | ❌ 不可用   |

**根本原因**：Android 12 起 Google 移除了内部 API `registerFrameworkNatives`（位于 `libandroid_runtime.so`），Frida 依赖此 API 加载 Java helper。

## 四、绕过方案

### 方案 A: LSPosed (推荐)

LSPosed 使用 Xposed API，独立于 Frida 的 Java bridge。

```bash
# 已安装 Magisk 模块
/data/adb/modules/zygisk_vector/    ← Zygisk 基础
/data/adb/lspd/                      ← LSPosed daemon
```

适用场景：真机，需要长期稳定的 Java hook。

### 方案 B: 直接 JNI 调用 (Frida JS)

不依赖 Frida Java bridge，直接在 JS 里用 `NativeFunction` 包装 JNI 函数：

```javascript
// java_jni_direct.js — NativeFunction 直接调 JNI
var libart = Process.findModuleByName("libart.so");
var JNI_GetCreatedJavaVMs = new NativeFunction(
    libart.findExportByName("JNI_GetCreatedJavaVMs"),
    "int", ["pointer", "int", "pointer"]
);
var vmBuf = Memory.alloc(8);
var countBuf = Memory.alloc(4);
JNI_GetCreatedJavaVMs(vmBuf, 1, countBuf);
var javaVM = Memory.readPointer(vmBuf);

// AttachCurrentThread 获取 JNIEnv
// 然后直接用 JNI 函数做 hook
```

**优点**：不修改 Frida 源码
**缺点**：需要手动管理 JNI 引用，极其繁琐

#### ★★ 方案 B 实战实现 (2026 验证可用: MuMu Android15, 无 Java bridge 下**调通任意 app Java 方法**)

> 实测：`nsv11` 与 `frida-server-official` **都** 报 `ReferenceError: Java is not defined`。
> 下面这套纯 JNI 已**调通「快手 sig3」**——证明无 Java bridge 也能调 app 的 Java 方法。

```javascript
var libart = Process.findModuleByName("libart.so");
// 1) JavaVM
var getVMs = new NativeFunction(libart.findExportByName('JNI_GetCreatedJavaVMs'),'int',['pointer','int','pointer']);
var vms=Memory.alloc(8), cnt=Memory.alloc(4); getVMs(vms,1,cnt);
var vm=vms.readPointer();
// 2) AttachCurrentThread = JNIInvokeInterface 偏移 4*ptr (idx4)
var attach=new NativeFunction(vm.readPointer().add(4*Process.pointerSize).readPointer(),'int',['pointer','pointer','pointer']);
var envBox=Memory.alloc(8); attach(vm, envBox, ptr(0));
var env=envBox.readPointer(); var net=env.readPointer();
function fn(i,r,a){ return new NativeFunction(net.add(i*Process.pointerSize).readPointer(), r, a); }
```

**★ 关键 1 — 新 attach 线程 classloader 是 boot 的，`FindClass` 找不到 app 类** → 必须走 **app 的 ClassLoader** 加载：
```javascript
var clsAT = FindClass('android/app/ActivityThread');
var app = CallStaticObjectMethod(clsAT, GetStaticMethodID(clsAT,'currentApplication','()Landroid/app/Application;'));
var cl  = CallObjectMethod(app, GetMethodID(GetObjectClass(app),'getClassLoader','()Ljava/lang/ClassLoader;'));
var loadClass = GetMethodID(GetObjectClass(cl), 'loadClass', '(Ljava/lang/String;)Ljava/lang/Class;');
// 之后所有类都: cl.loadClass("com.xxx.Yyy")   ← 不再用 FindClass
```

**★ 关键 2 — JNINativeInterface 索引 (每项 8 字节)**：
| idx | 函数 | idx | 函数 |
|---|---|---|---|
| 6 | FindClass | 144 | GetStaticFieldID |
| 15 / 17 | ExceptionOccurred / ExceptionClear | 145 | GetStaticObjectField |
| 31 | GetObjectClass | 167 | NewStringUTF |
| 33 | GetMethodID | 169 | GetStringUTFChars |
| 34 | **CallObjectMethod**(变参) | 172 | NewObjectArray |
| 113 | GetStaticMethodID | 174 | SetObjectArrayElement |
| 114 | **CallStaticObjectMethod**(变参) | 228 | ExceptionCheck |

**★ 关键 3 — 变参 `CallXxxMethod` 按实参个数分别声明**（Frida `NativeFunction` 参数个数必须精确）：
```javascript
var CallObj0=fn(34,'pointer',['pointer','pointer','pointer']);
var CallObj1=fn(34,'pointer',['pointer','pointer','pointer','pointer']);
var CallObj4=fn(34,'pointer',['pointer','pointer','pointer','pointer','pointer','int','pointer']); // (String,String,int,String)
```

**★ 关键 4 — 调 native 方法返 null 时，改调 app 的「高层 Java API」**：高层内部会处理 init / 线程态。
（快手实例：硬调 `JNICLibrary.doCommandNative(10418)` **全返回 null**；换成 app 自己走的高层 `MXSec.get().getMXWrapper().b(...)` **立刻出真值**。）

**读返回值**：String → `GetStringUTFChars(169)` + `readUtf8String`；读前先 `ExceptionOccurred(15)`+`ExceptionClear(17)` 清挂起异常（否则后续 JNI 调用行为异常）。

### 方案 C: 编译 DexClassLoader 补丁

修改 frida-core 源码，在 `do_start()` 中加入：

1. `JNI_GetCreatedJavaVMs` → 获取现有 VM
2. `AttachCurrentThread` → 获取 JNIEnv
3. `DexClassLoader(helper.dex)` → 加载 Frida 的 Java helper
4. `JNI_RegisterNatives` → 注册 JNI 方法

```vala
// 伪代码 — 需在 linux-host-session.vala 的 else 分支实现
if (get_java_vms != null && vm_count > 0) {
    (*vm)->AttachCurrentThread(vm, &env, NULL);
    // 用 DexClassLoader 加载 helper DEX
    // JNI_RegisterNatives 注册 native 方法
}
```

**优点**：完整 Java bridge 功能
**缺点**：需要重新编译 frida-server

## 五、Frida 源码位置

| 文件 | 路径 | 说明 |
|------|------|------|
| Java bridge 初始化 | `frida-core/src/linux/linux-host-session.vala` | `do_start()` 函数 |
| JNI 类型定义 | `frida-core/vapi/jni.vapi` | JNI 函数接口 |
| Java helper 源码 | `frida-core/src/android-helper/re/frida/Helper.java` | Java 端 helper |
| helper.dex | `frida-core/src/android-helper/helper.dex` | 编译后的 DEX |
| 编译配置 | `frida-core/src/android-helper/meson.build` | helper DEX 构建 |

## 六、当前 nosuke v2 状态

```
nosuke-server (Release QuickJS)
├── GetCreatedJavaVMs  → 已实现 (读符号，不执行)
├── registerFrameworkNatives → ❌ Android 12 已删除
├── DexClassLoader bypass → ❌ 未实现
└── 实际状态: Java bridge 不可用

影响: CU 刷量/快手/ATM Legend 均不需要 Java bridge
       Unity IL2CPP 游戏数值全在 C++ 堆
```

## 七、典型适用场景

| 场景 | 需要 Java bridge? | 替代方案 |
|------|------------------|---------|
| OkHttp 抓包 | ✅ | SSL_write hook |
| URLConnection 拦截 | ✅ | SSL_write hook |
| MessageDigest 加密日志 | ✅ | hook 算法本身 |
| Unity IL2CPP 游戏 | ❌ | Interceptor + ARM mem |
| Flutter 应用 | ❌ | Dart VM hook |
| 系统 Service Hook | ✅ | Xposed/LSPosed |
| SharedPreferences | ✅ | 直接读文件 |

---

## 八、★ 关键补充: frida **CLI** 自带 Java 桥 (2026-09-30 实测)

**结论: raw agent 无 Java 桥, 但 `frida` **CLI** 从**客户端侧**提供桥 → `Java.perform` 可用!**
（同为 nosuke 魔改 server, frida 17.11.0）

| 方式 | `typeof Java` | 结果 |
|---|---|---|
| Python `session.create_script(...)` | `undefined` | ❌ `ReferenceError: Java is not defined` |
| `frida -H host:port -e "..."` 或 `-l script.js` | `object` | ✅ `ava=true classes=41171` |

### 原理 (frida-tools 源码, 桥是**握手加载**的)
- 桥**不在 agent 二进制里**, 而是 agent 发消息 `{type:"frida:load-bridge", name:"java"}`, **客户端**回
  `{type:"frida:bridge-loaded", filename, source}`（见 `frida_tools/application.py:951 try_handle_bridge_request`）
- 桥源码 = `site-packages/frida_tools/bridges/java.js`（约 237 KB; 同目录还有 objc.js / swift.js）
- `repl.py` 的 CLI **自动应答**该握手 → 用户脚本里 `Java` 就绪
- **raw `create_script` 不应答** → 所以 Python 直连时 `Java` undefined

### 用法 (已实测)
```bash
frida -H 127.0.0.1:18081 -n com.smile.gifmaker -q -l hook.js      # 直接跑, Java 可用
frida -H 127.0.0.1:18081 -p <pid> -q -e "Java.perform(()=>console.log(Java.available))"
#   --auto-perform : 自动把 -e 代码包进 Java.perform
```
> 复刻握手（想在 Python 里用）: `sc.on('message')` 里判断 `payload.type=='frida:load-bridge'`,
> 然后 `sc.post({type:'frida:bridge-loaded', filename:'java.js', source:<bridges/java.js 内容>})`。
> （注意: 单纯把 java.js 前置拼接**不生效**, 必须走握手）

### 与纯 JNI（方案 B）的取舍
- **CLI 桥**: `Java.use/perform` 写起来简单; 但依赖 frida-tools, 高版本 frida 暴露面更大
- **纯 JNI**: 零客户端依赖、更隐蔽（魔改/反检测场景首选）; 但繁琐
- 实战: 抓包 / 调 app Java 方法 **先试 CLI 桥**; 被检测或要零痕迹时退回纯 JNI

> ⚠️ **修订**: §六 "Java bridge 不可用" 仅对 **raw agent / Python API** 成立; **CLI 路径可用**。
