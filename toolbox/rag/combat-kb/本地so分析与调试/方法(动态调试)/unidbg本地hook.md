# 方法(动态调试)：unidbg 本地执行 + Hook

> 定位：不开真机 / 模拟器，用 unidbg 在 Windows 直接加载 `.so` 调函数 / hook。
> 核心价值：反复跑、改输入、改 hook 逻辑，秒级重试，且**不触发反调试**（本地模拟）。
> 判据驱动，**不举具体实例**。
> 环境：JDK + unidbg（`D:\逆向工具\unidbg\`，mvn 编译）

---

## 一、原理：真机 Frida ↔ unidbg 映射

```
真机 Frida                      unidbg 本地
────────────────────────       ──────────────────────
Interceptor.attach(addr, cb) → HookZz.replace(addr, ReplaceCallback)
Java 方法 hook                 → MyJni extends AbstractJni
send(data)                     → print() / 写文件
frida -H host:port             → java Demo（本机运行）
```

**不需要**：模拟器 / root / frida-server / adb / 端口转发。
**无惧**：`ptrace` 反调试、`maps` 扫描、root 检测（本地模拟不触发）。

---

## 二、能做什么 / 不能做什么

| 能 | 不能 |
| --- | --- |
| hook 加密函数抄参数（key / iv / data）| ❌ 真实网络 / 系统调用 |
| 调 JNI 函数看输入输出 | ❌ UI 交互 |
| 读内存、检查 GOT 绑定 | ❌ 超大 / 代码加密的库 |
| 反复跑、改输入、秒级重试 | — |
| 绕过反调试 | — |
| 验证 key 是硬编码还是动态 | — |

---

## 三、核心流程

```
1. 静态定目标
   ├─ readelf / strings 定位函数偏移
   ├─ idat / capstone 反汇编
   └─ 选 hook 点（加解密 / 网络读写 / 打包）

2. 构造 unidbg 环境
   ├─ AndroidEmulatorBuilder.for64Bit()
   ├─ createDalvikVM(<真实APK>)
   └─ loadLibrary(target.so)

3. HookZz.replace(hook_addr, callback)
   └─ callback 里取参（ctx.getPointerArg(n)）

4. 调目标函数
   ├─ callFunction("Java_xxx", args...)
   ├─ 或等 so 内部触发
   └─ 看 callback 输出
```

---

## 四、最小工作骨架

### 4.1 造模拟器 + 加载 SO
```java
AndroidEmulator emulator = AndroidEmulatorBuilder.for64Bit()
        .setProcessName("<包名>").build();
Memory memory = emulator.getMemory();
memory.setLibraryResolver(new AndroidResolver(23));

VM vm = emulator.createDalvikVM(new File("<真实APK路径>"));
vm.setVerbose(true);
vm.setJni(new MyJni());            // 补 Java 回调

DalvikModule dm = vm.loadLibrary(new File("<target.so>"), false);
Module module = dm.getModule();
vm.callJNI_OnLoad(emulator, module);
```

### 4.2 HookZz 替换（本地 hook）
```java
import com.github.unidbg.hook.hookzz.*;

long hookAddr = module.base + <偏移>;
HookZz hookZz = HookZz.getInstance(emulator);
hookZz.replace(hookAddr, new ReplaceCallback() {
    @Override
    public HookStatus onCall(Emulator<?> emu, HookContext ctx, long addr) {
        // ARM64 取参：x0=第1参, x1=第2参 ...
        Pointer arg1 = ctx.getPointerArg(1);
        int     len2 = ctx.getIntArg(2);
        byte[] data = arg1.getByteArray(0, Math.min(len2, 4096));
        System.out.println("[hook] len=" + len2 + " data=" + Hex.toHexString(data));
        return HookStatus.LR(emu, 0);   // 返回，不修改原逻辑
    }
});
```

### 4.3 调 JNI 函数
```java
// 例：调一个 (byte[] key, byte[] iv, byte[] data) 的 JNI
int keyObj  = vm.addLocalObject(new ByteArray(vm, key));
int ivObj   = vm.addLocalObject(new ByteArray(vm, iv));
int dataObj = vm.addLocalObject(new ByteArray(vm, data));

Number r = module.callFunction(emulator,
    "Java_<...>",
    vm.getJNIEnv(),
    0,                       // jclass（静态方法）
    keyObj, ivObj, dataObj);

DvmObject<?> result = vm.getObject(r.intValue());
byte[] out = (byte[]) result.getValue();
System.out.println("out: " + Hex.toHexString(out));
```

### 4.4 读内存 / 检查 GOT
```java
long gotBase = module.base + <rw段vaddr>;   // LOAD rw 段基址
long val = memory.pointer(gotBase + <槽偏移>).getLong(0);
if (val == 0) System.out.println("GOT 槽未绑定");
```

---

## 五、hook 外部依赖（HookZz + GOT 懒绑定）

so 通过 PLT/GOT 调外部 API（资源、文件、系统服务）。两种兜底：

```java
// (a) 直接 replace 目标函数
HookZz.getInstance(emulator).replace(base + <目标偏移>, new ReplaceCallback() {
    @Override public HookStatus onCall(Emulator<?> emu, HookContext ctx, long addr) {
        // 取参 -> 造返回值 -> 返回
        return HookStatus.LR(emu, ret);
    }
});
```

```java
// (b) GOT 懒绑定双保险：so 走 blr-GOT 绕过 PLT hook 时，直改 GOT 槽
long[] slots = { <GOT槽1>, <GOT槽2>, ... };
long[] stubs = { <替换1>, <替换2>, ... };
long gotBase = base + <rw段vaddr>;
for (int i=0; i<slots.length; i++)
    memory.pointer(gotBase + slots[i]).setLong(0, base + stubs[i]);
```

> 判据：hook 后目标函数**确实被调用**（回调触发）。不触发 → 查是否走了 GOT 懒绑定。

---

## 六、补 Java 回调（MyJni）

SO 里 JNI 代码会反向调 Java，unidbg 里全得你演：

```java
public class MyJni extends AbstractJni {
    @Override
    public DvmObject<?> callObjectMethod(BaseVM vm, DvmObject<?> obj, String m, VarArg va) {
        if ("getPackageName".equals(m)) return new StringObject(vm, "<包名>");
        return super.callObjectMethod(vm, obj, m, va);
    }
    @Override
    public DvmObject<?> callStaticObjectMethod(BaseVM vm, DvmObject<?> obj, String m, VarArg va) {
        if ("getProperty".equals(m)) return new StringObject(vm, "unknown");
        return super.callStaticObjectMethod(vm, obj, m, va);
    }
}
```

**方法论**：so 卡在哪，`verbose` 日志会打 `[JNI] callXxxMethod ...`，缺哪个补哪个，循环补到跑通。
**签名 / 包名校验**：`createDalvikVM(<真实APK>)` 让 signatures 自动过。

---

## 七、与设备端 Frida 的联合

| 场景 | 本地（unidbg）| 设备（Frida）|
| --- | --- | --- |
| 跑纯算法函数 | ✅ 首选 | ❌ 开机麻烦 |
| 跑需要网络的函数 | ⚠ 需补环境 | ✅ 原生支持 |
| 快速验证 key 来源 | ✅ 秒级 | ❌ 附加慢 |
| 大规模反复测试 | ✅ 本地 | ❌ 每次 attach 耗时 |
| 超大 / 加密库 | ❌ | ✅ 动态 |
| 系统调用分析 | ❌ | ✅ strace |

**推荐流程**：unidbg 本地验证 key 来源 / 参数格式（反复调）→ Frida 设备上 hook 网络层。

---

## 八、常见问题

| 问题 | 对策 |
| --- | --- |
| SO 卡在 JNI 回调不动 | `verbose` 看缺哪个回调，MyJni 补上 |
| HookZz 回调不触发 | GOT 懒绑定绕过 → 直改 GOT 槽指向 stub |
| `byte[]` 参数传错 | 用 `ByteArray(vm, bytes)` + `addLocalObject` |
| 回调里 malloc 假对象崩 | 在 main 里**预分配**（如 0x200 字节） |
| SO 依赖 assets 文件 | hook 资源管理器 open/read |
| SO 早退 | 补齐包名 / 签名 / 设备信息回调 |
| `classpath` 丢 jar | 照 `run.cmd` 的完整 jar 清单 |
| NoClassDef | 全套 m2 jar + target/classes |
| **加载即崩, PC=小偏移(如 0x6bf0)** | **`.rela.plt` 未绑定**：首次调 import 走 PLT[0] 解析器 → 跳到未加基址的 PLT 偏移。unidbg 未应用 `.rela.plt`(JUMP_SLOT)，GOT 槽仍是链接期占位值。对策：换 **Qiling 手动补重定位**，或 Java 侧 `module.getRelocations()` 逐条绑 GOT |

> ★ 实测(快手 libksse.so)：`LDR X8,[X8,#0x6B8]`=RegisterNatives；`.rela.plt` 132 条未绑 → PC=0x6bf0 崩。
> **Qiling 手动应用 `.rela.dyn`+`.rela.plt`(GOT→ret 桩) 可绕过**（见 `../实战案例/快手Android签名.md` §3）。
