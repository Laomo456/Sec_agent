# 实战: 快手 Android 签名逆向（本地静态 + Qiling 仿真）

> 2026 实战 | 目标: `com.smile.gifmaker`（快手主App，arm64，30 dex，134 so）
> 定位: **补全本库「方法(动态调试)」的实操** + 记录 unidbg/Qiling 新踩坑
> 产出: sig 算法（闭环验证）+ 签名体系全图 + Qiling 仿真模板

---

## 一、签名体系（请求头）

| 参数 | 算法 | 层 |
|---|---|---|
| `sig` | `MD5( join("", sorted(f"{k}={v}")) + "382700b563f4" )` | libcore.so `CPU.getClock` |
| `sig2` | 同 sig（埋点带 bodyMd5） | 同上 |
| `__NS_sig3` | `KSecurity.atlasSign(path+sig)` → JNICLibrary.doCommandNative | libksse.so/libkwsgmain.so |
| `__NS_xfalcon` | XGS `b.a(...)` → `GNative` | libksxgs.so |
| `__NStokensig` | `sha256Hex(sig + client_salt)` | 纯 Java |

**定位链（Java→native）**：
```
com.yxcorp.retrofit.interceptor.CommonParamsInterceptor
  → d0a.k(ckm.s).a(req,salt)  [KwaiSignSupplierImpl]
  → alm.g/alm.a 构造 sig/__NS_sig3/__NS_xfalcon/__NStokensig
  → alm.k 实现(d0a.k$d): getSig=CPU.getClock, b=qz9.p.c(path+sig1), a=qz9.p.b(path,txt), d=sha256Hex
  → CPU.getClock → libcore.so ; qz9.p.c → KSecurity.atlasSign → libksse/libkwsgmain
  → qz9.p.b → XGS b.a(...) → libksxgs.so
```

## 二、sig 破解手法（可复用）

1. jadx 定点反编译 classes4/6/12/20 → 找 `getSignatureParamsMap` 实现
2. `alm.k` 实现类 `d0a.k$d`：`getSig` 调 `CPU.getClock(app, plaintext.getBytes(), sdkInt)`
3. `CPU` 类 static 块 `System.loadLibrary("core")` → **libcore.so（仅 22KB！）**
4. idat 反编译 `Java_com_yxcorp_gifshow_util_CPU_getClock` → 见 `sub_245C/2480/2840` = **MD5 Init/Update/Final**（IV `0x1032547698BADCFE` 实锤标准 MD5）
5. 盐 = `sub_142C(&unk_3570, 16)`（自研 S-box 分组密码，仅用 malloc/free）
6. **unidbg `module.callFunction(emulator, 0x142C, base+0x3570, 16)`** → 解出盐 `382700b563f4`
7. **闭环验证**：unidbg 调该 so 的 MD5 vs `hashlib.md5`（abc/hello/空串三例全等）→ 确认标准 MD5

> 关键经验：**别手抄密码学**。发现疑似哈希就 unidbg `callFunction` 拿现成实现，用标准库对比验证。

## 三、★ 新踩坑（补充 unidbg/Qiling 篇）

### 3.1 unidbg：`.rela.plt` 惰性绑定 → PLT[0] 崩 (重要!)
- 现象：`callJNI_OnLoad` 后首次调 import → `PC=0x6bf0`（PLT 跳板偏移，**未加基址**）未映射崩
- 根因：**unidbg 未应用 `.rela.plt`（JUMP_SLOT）**，GOT 槽保留链接期占位值（= 该库 PLT 跳板偏移）
- 排查：`.rela.plt` 有 132 条（getpagesize/memcpy/fopen/_Unwind_*...）；`.rela.dyn` 无 RELR
- 影响：**所有用 PLT 惰性绑定的 so 都会中招**

### 3.2 Qiling：手动补重定位 → 治好 (解法)
- 见 `qiling_tprt_sim.py` 模式：先给所有 undef 符号分配 `ret` 桩，再应用 `.rela.dyn`(RELATIVE=base+addend) + `.rela.plt`(GOT→桩)
- Qiling 不自动应用 → 必须手动（本库「Qiling本地执行.md」第四节）

### 3.3 Qiling：JNIEnv 全 stub 的坑
- `FindClass/GetMethodID/NewStringUTF` 桩返回 0 → 上层 `[0+0x24]=x` 崩
- 解法：JNIEnv functions 表按 idx 设专用桩；**关键 idx 返回非空假对象**：
  `[6 FindClass, 21 NewGlobalRef, 25 NewLocalRef, 31 GetObjectClass, 33 GetMethodID, 34 GetStaticMethodID, 94/113/114, 144/165/167 NewStringUTF, 169-173, 219/220, 226]`
  `215 RegisterNatives` 保留给捕获桩（读 `(env,clazz,methods[],n)`，JNINativeMethod=24B{name,sig,fnPtr}）

### 3.4 Qiling：C++/alloc/TLS 桩必须真实现
- `_Znwm/_Znam`(operator new) 返回 0 → C++ 分配失败 → JNI_OnLoad 返回 -1
- `malloc/calloc/memcpy/memset/strlen/strcmp/sprintf` 需真实现（sprintf 要解析 `%s/%d/%x` 格式）
- `pthread_key_create/getspecific`：getspecific 返回 0 → 上层解引用崩；应返回**非空假 TLS 块**

### 3.5 idat：trampoline（JUMPOUT）要强制建函数
- `JNI_OnLoad` 常编译成 trampoline：`STP...; ADR X1,...; BR X9` → IDA 标 `JUMPOUT(0x...)`
- 目标地址 IDA 不自动建函数 → 反编译得 "(null)"
- 解法：`ida_funcs.add_func(va)` 后再 `decompile`；或 `idc.create_insn` 逐条强制反汇编
- 反混淆：快手安全 so 大量**控制流平坦化**（`while(1) switch(state)`），反编译可读但需手工跟 state

## 四、快手安全 so 结构（供同类参考）

| so | 大小 | 特点 |
|---|---|---|
| libcore.so | 22KB | CPU.getClock/getMagic（MD5+salt）**直接导出** |
| libksse.so | 421KB | ksse 引擎；导出 `raw_syscall`；JNI_OnLoad 成功返回 65540，注册 2 native |
| libkwsgmain.so | 455KB | `kssecurity::`：SecurityParser/PassTable/各 Detector；**`EncStr` 串加密** + `x_*` 裸 syscall 引擎 |
| libksxgs.so | 250KB | XGS 网关；14 个 `Java_com_kjmp_framework_kjbox_j_n_a0..a13`；JNI_OnLoad 平坦化 |

**识别要点**：方法名可能运行时经 `EncStr` 解密（`.rodata` 看不到明文）；`JNINativeMethod` 表在**栈上运行时构造**（静态无表）→ 必须 hook RegisterNatives。

## 五、工具链

| 工具 | 用法 |
|---|---|
| idat | `idat -A -S"script.py" target.so`（-A 批处理+建库） |
| unidbg | `module.callFunction(emulator, off, args...)` 调任意函数 |
| Qiling | 源码版 + 手动重定位（见 3.2） |
| jadx | `jadx --no-res -d out x.dex` |

---

## 六、反调试/检测画像（本地逆向，先画像再绕过）

> 原则（KB 反检测方法论）：**观测永远安全，修改才有风险；先画像 → 再绕过 → 最后 hook**。

### 6.1 检测器类（libkwsgmain.so，`kssecurity::` 命名空间，字符串实证）
```
kssecurity::Detector (基类)  →  AndDetector
  ├─ AntiDebug            (/proc/self/maps, x___ptrace, rt_sigprocmask/rt_sigsuspend 信号族)
  ├─ HookDetector         (de.robv.android.xposed.installer, com.saurik.substrate, /proc/self/maps)
  ├─ RootDetector         (/system/xbin/su, /system/bin/su, /sbin/su, /su/bin/su, /bin/su,
  │                        com.genymotion.superuser, /proc/net/route)
  ├─ EmulatorDetector     (vbox86/vbox86p, Genymotion, ro.genymotion.version, Init.svc.vbox86-setup,
  │                        com.bluestack.BstCommandProcessor, /dev/socket/qemu, /dev/qemu_pipe,
  │                        /system/lib/libc_malloc_debug_qemu.so, /sys/qemu_trace, ro.build.host)
  ├─ NativeEmuDetector
  ├─ RePackDetector       (retry uncompress from apk success)
  └─ MalwareDetector
辅助: PkgTool / DataEntry / PassTable / SecRuntimeContext / HostConfig / SecurityParser
```
libksse.so 采集器：`ICpuinfoCollector`(`/proc/cpuinfo`, `/proc/asound/card`, `ro.product.*`, `ro.build.*`) / `ICollector` / `NetworkCollector` / `SysCollector` / `ManuCollector`（=设备指纹）。

### 6.2 ★ x_* 裸 syscall 引擎（反 hook 关键）
libkwsgmain.so 自带一套 `x_*` 系统调用封装，**绕过 libc**：
```
x___ptrace  x_process_vm_readv  x_process_vm_writev  x_memfd_create
x_getresuid x_setfsuid x_setresuid x___getcpu  x___rt_sigprocmask x___rt_sigsuspend
```
→ **Frida hook libc 的 ptrace/vm_readv 无效**（它不经过 libc）。要拦得 hook `svc #0` 指令或这些 `x_*` 封装。
→ AntiDebug 用 `rt_sigsuspend/rt_sigprocmask`（信号族反调试）+ 自查 `/proc/self/maps`（扫 frida memfd/gum）。

### 6.3 本地拟真取数（无真机）
- **Qiling**：跑 init/JNI_OnLoad → 看它读哪些 `/proc/*`、syscall 路径（auto-map 跑到底）
- **unidbg**：真 JNIEnv 下**调 native 检测方法**、`hook RegisterNatives` 捕获接口、**枚举 EncStr 取串函数**得全字符串表
- 产出 = **检测点清单 + hook 落点**；真机/模拟器只做验证（见 KB `模拟器崩溃以及反检测/`）

---

## 七、★ EncStr 字符串加密破解（本地拟真闭环）

> 快手安全 so 的方法名/关键串经 `EncStr` **运行时解密**（`.rodata` 无明文）。破解 = 静态反编译取串函数 + unidbg 校验 + Python 复现。

### 7.1 取串函数（idat 反编译 libkwsgmain.so）
```
sub_3CEEC(blob):                       # EncStr::get
  pthread_mutex_lock(&stru_72880)
  sub_3D0F0()                          # reset: A/B/C = 0x13579BDF/0x2468ACE0/0xFDB97531
  sub_3D258("Vuz4fCHxn1CO")            # seed: key[4..7]="fCHx" 混入三寄存器
  n = blob[0]; buf = calloc(n+1)
  for i in 1..n: buf[i-1] = sub_3D4E4(blob[i])   # 逐字节
  return buf
sub_3D4E4(c): 8 轮 LFSR(三寄存器) -> v14;  return (v14+3) ^ c
```
**密码**：自研 3 寄存器 LFSR 流密码；`plain = (ks+3) ^ cipher`；blob = `[len][cipher...]`。

### 7.2 校验（unidbg HookZz — 拟真取真值）
```java
HookZz.getInstance(emulator).wrap(base+0x3D4E4, new WrapCallback<HookZzArm64RegisterContext>(){
   long in; public void preCall(...){ in = ctx.getXLong(0); }
   public void postCall(...){ long out = ctx.getXLong(0); /* in^out = ks+3 */ }});
vm.callJNI_OnLoad(emulator, m);        // 拿真机 (密文,明文) 对
```
→ 与 Python 复现的 ks 序列**逐字节吻合** = 密码正确。

### 7.3 爆破全表（扫 .rodata）
对 `.rodata` 每个偏移：读 `blob[0]=len`，解密 `blob[1..len]`，全可打印即命中 → 出 85+ 条隐藏串。
脚本：`C:\Users\20751\kwai_work\scan_encstr.py`

**关键收获**：
| 串 | 含义 |
|---|---|
| `5FDxA9ngHE723pYw` / `12345678kuaishou` | atlas 签名 key/种子（`@0x70000`） |
| `sha256key00_64` / `TBoxes_40960` / `TyiTables_4096` / `custom_2018.png` / `yh_2018.png` | atlas 密码表（从 APK png 加载） |
| `atlasSignature failure` / `AtlasHMACEncrypt` / `AtlasAESEncrypt` | atlas 签名错误串 |
| `TracerPid:` / `ptrace_stop` / `/proc/self/wchan` / `being debugged` | AntiDebug |
| `XposedBridge.jar` / `disableHooks` / `Uninstall Xposed Hook success...` | HookDetector |

> **方法论要点**：字符串加密 ≠ 死路。**取串函数是单点**——反编译它 → unidbg 拟真校验 → Python 复现 → 全表爆破。比动态 hook 更稳、更全。
