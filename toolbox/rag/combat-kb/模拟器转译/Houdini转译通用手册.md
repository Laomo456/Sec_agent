# 模拟器转译 (Houdini / NativeBridge) 通用手册

> x86_64 模拟器跑 ARM app 的通用打法：定性 → 坑 → 布局 → 取证 → 提取 → 决策。
> 只记**通用规律**，不含具体样本。

---

## 一、一句话

x86_64 模拟器跑 ARM app = **两个 zygote**：主 zygote 管原生，`zygote_secondary` 管转译。
app 里的 so **一半原生一半转译是常态**——别猜，**看 ELF 头 / 看 maps**。

---

## 二、30 秒定性（三连）

```sh
# 1) so 是什么架构：第 18 字节 3e=x86_64 原生 / b7=aarch64 转译
xxd -l 20 libxxx.so

# 2) 进程里有没有转译层在场
grep -a houdini /proc/<pid>/maps

# 3) 运行时到底加载了哪份（磁盘 .so 的 machine ≠ 内存那份）
grep libxxx /proc/<pid>/maps
```

**crash 归因**（logcat DEBUG 段）：
- `rip` 落在 houdini 区间 → 转译代码崩
- `invalid arm reg` → 是 **x86_64 原生**代码崩，转译只是围观打印，别冤枉它
- `houdini arm64 regs base` → 只是 houdini 自己的上下文输出

---

## 三、常见坑

1. **同一 app 混 ABI**：Java 层走转译，某些 native 库却 x86_64 直跑；壳还会把不同 ABI 的 so 解到不同目录。**磁盘那个 .so 的 machine 字段 ≠ 运行时实际加载的那份**，先 `grep libxxx /proc/<pid>/maps` 确认。
2. **性能**：ARM so 走 Houdini，计算密集（加密/解码）慢 **5–20×**；抓包超时 / 竞速类对抗先怀疑转译拖的。
3. **crash 栈不可直接读**：Houdini 有自己的信号处理链，SIGSEGV 栈帧 PC 指向转译区而非业务代码。
4. **vdex/oat 等 rw 文件映射**：内存 dump 扫东西时这种段别漏——rw 的文件映射经常装着好货。
5. **Java 堆双地址风格**：低地址段与高地址段都是堆，扫内存**两边都得扫**。
6. **依赖自身 native 的工具**（虚拟容器类）在转译环境里加载 ARM 目标容易 **ABI 失配直接 NPE**——不是你的错，是工具的。
7. **`dex2oat` 报 `Unexpected CPU variant for x86: x86_64`** 是正常警告，不用管。

---

## 四、排查一条龙

```sh
ps -A | grep <pkg>                 # 找 pid
grep -a houdini /proc/<pid>/maps   # 转译层基址
cat /proc/<pid>/maps               # 原生段 / 转译段 / houdini 基址全在这
# root 下 dd /proc/<pid>/mem 不触发 TracerPid；段偏移 = addr//4096
```

---

## 五、ARM SO 加载路径（Houdini）与 hook 点

```
System.loadLibrary("xxx")
  → ART dlopen → linker 读 ELF e_machine
    → 发现 ARM64（不匹配 x86_64）
      → 不走 dlopen！
        → NativeBridgeItf->loadLibraryExt()   ← hook 这个
          → Houdini 翻译 + 加载
```

⇒ **hook `fopen`/`openat`/`dlopen` 永远抓不到 ARM SO 加载**，必须 hook **NativeBridge**。

### NativeBridgeItf 结构体（Android 12+）

```javascript
var nb = Module.findExportByName('libhoudini.so', 'NativeBridgeItf'); // 即结构体起始地址
nb.readInt()                 // version（实测 7）
nb.add(0x08).readPointer()   // initialize
nb.add(0x10).readPointer()   // loadLibrary
nb.add(0x18).readPointer()   // getTrampoline
nb.add(0x20).readPointer()   // isSupported
nb.add(0x70).readPointer()   // loadLibraryExt   ← Android 8.0+ 推荐
```

### hook 模板

```javascript
function hookNativeBridge(){
  if (!Process.findModuleByName('libhoudini.so')) { console.log('[!] no libhoudini'); return; }
  var nb = Module.findExportByName('libhoudini.so', 'NativeBridgeItf');
  if (!nb) { console.log('[!] no NativeBridgeItf'); return; }
  [['loadLibrary',0x10], ['loadLibraryExt',0x70]].forEach(function(p){
    var fn = nb.add(p[1]).readPointer();
    Interceptor.attach(fn, { onEnter: function(a){ try { console.log('['+p[0]+'] ' + a[0].readCString()); } catch(e){} } });
  });
}
setImmediate(hookNativeBridge);
```

### ARM SO 在 maps 中的特征

```
400020e00000-400020e03000 r--p ... /lib/arm64/libxxx.so    ← 0x4000xxxxxxxx 区间
400020e13000-400020fd3000 rw-p ... /lib/arm64/libxxx.so    ← 数据段
```
- 地址落在 **`0x4000xxxxxxxx`** 区间
- 段权限多为 **`r--p`**（Houdini 只读原始 ARM 页；真正执行的是它译出的缓存）
- frida `Process.findModuleByName()` **找不到**（只认 x86_64 模块表）
- 用 `/proc/PID/maps` 算地址，再 `ptr(addr).readByteArray()` 读（**读**可以，**写/hook**另说，见 §六）

---

## 六、Houdini 内存布局（通用）

```
[ x86_64 原生区 — 0x7xxxxxxxxx ]
  rwxp   x86 原生 so 代码段（含壳自解密 / 自修改代码）
  rw-    匿名 → 解密后的 dex / 数据
  ---    1GB 匿名保留（dalvik-LinearAlloc）
[ ARM64 转译区 — 0x4000xxxxxxxx ]
  400000000000-…   rw-   ARM64 堆 / 数据
  400020000000-…   ---+r--  ARM64 用户态镜像（/system/lib64/arm64/*.so + 各 ARM so）
  各 ARM so:  r-- 代码段 / rw- 数据段
[ 翻译缓存 — rwxp 匿名 ]
  主缓存 [anon:Mem_0x…]（百 MB 级）— 内容 = x86_64 机器码
```

### 翻译缓存（Translation Cache）要点

- Houdini 把 ARM64 指令**逐条译成 x86_64**，缓存进 **rwxp 匿名区**（**写时翻译**：执行到才译、译完直接执行）
- 缓存里是 **x86_64 机器码**，不是 ARM
- ⇒ 对 ARM 原始页做 **inline hook 常常打不到**（真正跑的是缓存），这是转译环境 hook ARM 不生效的根因

### 区分原生 / 转译 so（三法）

| 方法 | 转译 (ARM) | 原生 (x86_64) |
|---|---|---|
| maps 地址 | `0x4000xxxx` 区 | `0x7xxx xxxx` 区 |
| `findModuleByName` | 找不到 | 找得到 |
| maps 文件名 | `/system/lib64/arm64/xxx` | `/system/lib64/xxx` |

---

## 七、内存取证：SELinux permissive + `/proc/PID/mem`（通用）

**问题链 → 正解**：

| 手段 | 结果 |
|---|---|
| frida `readByteArray` / `process_vm_readv` | ❌ Permission denied（内核 + SELinux 拦） |
| frida `Memory.scanSync` | ✅ 能扫到 magic，但**读不出内容** |
| `dd if=/proc/PID/mem`（默认） | ❌ I/O error（SELinux 拦） |
| **`setenforce 0` 后再 dd** | ✅✅✅ 任意内存段直接读 |

```bash
# 关 SELinux（需 root）
su -c 'setenforce 0; getenforce'      # → Permissive

# 从 /proc/PID/mem 按地址 dump（bs=4096 时：偏移 = 虚拟地址 / 4096）
su -c 'PID=<pid>; dd if=/proc/$PID/mem of=/data/local/tmp/out.bin \
      bs=4096 skip=<addr//4096> count=<size//4096>'
```

**⚠️ 致命坑：mksh 的 `$(( ))` 是 32 位**
```bash
# 错：高 32 位被截断，skip 完全错位
dd ... skip=$((0x7bdb71558000/4096))          # 被截成 0x71558000
# 对：外部算好十进制（python -c "print(0x7bdb71558000//4096)"）
dd ... skip=33247663448 count=3663
```
**验证**：dump 完先看头部 magic（dex=`dex\n`，vdex=`vdex`，ELF=`\x7fELF`）。

**⚠️ SELinux 收紧（Android 15+）**：`/proc/<pid>/mem` 即使 root 也被拦 ⇒ 必须 `setenforce 0`，或改走**进程内 `readByteArray()`**。

---

## 八、从内存提取「解密后的 dex / 代码」（通用方法）

加密壳的通用形态：**磁盘密文 → 运行时在匿名 `rw-` 内存解密**。提取四步：

1. **定位**：frida `Memory.scanSync` 搜**结构特征**（如 dex magic `dex\n035` / `038` / `039`）
2. **过滤**：`class_defs_size > 500`（排除小 dex / 框架 jar）+ `file_size` 合理上限
3. **落盘**：算好**十进制** skip → `setenforce 0` + `dd`
4. **验证**：`head -c 8 | xxd` 看 magic 对不对

```javascript
// 结构特征扫描（伪代码）
Memory.scanSync(ptr('0x0'), 0xffffffff, '64 65 78 0a 30 33').forEach(function(m){
  var dv = new DataView(m.address.readByteArray(0x70));
  var cls = dv.getUint32(0x60, true);   // class_defs_size
  var fsz = dv.getUint32(0x20, true);   // file_size
  if (cls > 500 && fsz < 60000000) console.log(m.address + ' cls=' + cls + ' size=' + fsz);
});
```

**核心教训：搜代码别搜字符串，搜结构特征。**
方法体经 ART 处理后**不含原类名字符串**（被编译成方法索引），按字符串搜 = 永远找不到真实方法体。

### 壳「自解密」特征（怎么判断内存里已被解密）

对比 **磁盘 vs 内存**同名 so：

| 指标 | 加密态（磁盘） | 解密态（内存） |
|---|---|---|
| 熵 | 低（~4.x） | 高（~6.x） |
| ASCII 字符串数 | 少 | 显著增多 |
| 符号 | — | 新增导出/近 ART 的符号 |

出现 `DexFile::OpenMemory`、`InMemoryDexClassLoader`、`DexPathList`、`dalvik/system/DexFile` 等符号 ⇒ 证明壳走
**ELF 自解压 → `art::DexFile::OpenMemory` 内存建 dex → 注册 ClassLoader**。

---

## 九、决策顺序：多数时候**不必**硬 hook ARM SO

> inline hook 转译 ARM 是**下策**（缓存 rwxp、打不到、壳/TP 校验会改指令、存在时序问题）。
> 目标若是**算法 / 业务值**，优先**上探 Java 层**——**ART 是 x86_64 原生**，可绕过转译。

1. **能定位到 Java 层方法** → 走 **纯 JNI 直调 / Java hook**（ART x86_64，无转译问题）。
   - 无 frida Java bridge（Android 12+）→ 用**纯 JNI**（见 `Java bridge/README.md` 方案 B 实战）。
2. **只有 native、且是纯算法**（无 JNI 回调）→ 才考虑 **Qiling / unidbg 本地跑**（喂 buffer）。
3. **必须**在设备跑 native → 才 hook `NativeBridgeItf` / maps base + `Interceptor`。

---

## 十、陷阱汇总

| 陷阱 | 表现 | 原因 |
|---|---|---|
| frida 找不到 ARM SO | `findModuleByName` 返回 null | frida 只读 x86_64 模块表 |
| frida 读不了 `r--` 段 | `readByteArray` → Permission denied | SELinux + process_vm 双拦 |
| dd skip 读错 | dump 出垃圾 | mksh `$(( ))` 32 位截断 |
| spawn 失败 | NullPointerException | 简化版 frida 无 PackageManager |
| `Java.perform` 不可用 | "Java not defined" | frida 只挂 native 层 |
| `am start -D` 不冻结 native | native init 跑完才等 jdb | debug-wait 只冻 Java 层 |
| crash 栈乱 | PC 指向 houdini 翻译缓存 | 先看 rip 落在哪个段 |
| **hook ARM 不生效** | `onEnter` 永不触发 | 执行的是**翻译缓存**，不是原始 ARM 页 |
