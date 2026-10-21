# MuMu 模拟器调试实战知识库

> 更新时间：2026-09-14
> 样本：供应链金融 app（com.yaodu.supplychain），爱加密加固
> 模拟器：MuMu 6.0（Android 15，x86_64，Houdini ARM 转译）

---

## 一、环境概览

| 项目 | 值 |
|---|---|
| 模拟器 | MuMu Player 6.0 (nx_main v7.33) |
| Android | 15 (API 35) |
| 架构 | x86_64（ARM64 通过 Houdini 转译） |
| ADB 端口 | 127.0.0.1:7555 |
| Frida 版本 | 17.11.0（nosuke 反检测版） |
| Root | 可用（`su` 到 `#`） |

---

## 二、核心限制与原因

### 2.1 Frida spawn 不可用 (NullPointerException)

```
frida -H 127.0.0.1:8080 -f com.yaodu.supplychain
→ Failed to spawn: java.lang.NullPointerException: 
  Attempt to invoke virtual method 'android.content.pm.ApplicationInfo
  android.content.pm.PackageManager.getApplicationInfo(java.lang.String, int)'
  on a null object reference
```

**原因**：nosuke frida-server 以 root 运行，拿不到 Android PackageManager 服务。  
**影响**：无法在 app 启动前安装 hook，只能 attach 到已启动进程。

### 2.2 Java Bridge 不可用 (Java not defined)

```
Java.perform() → ReferenceError: 'Java' is not defined
```

**原因**：[推测] nosuke 反检测版本可能阉割了 Java 支持，或 x86_64 模拟器下 ART 内部函数地址与 frida 预期不符。  
**影响**：不能使用 `Java.enumerateLoadedClasses`、`Java.use` 等高级 API。  
**替代**：通过 libart.so / libdexfile.so 的 native 函数拦截。

### 2.3 ARM64 模块不可见

```
Process.findModuleByName('libdexjni.so') → null
```

**原因**：ARM64 .so 文件通过 Houdini 二进制翻译运行，在 x86_64 进程的模块表中不注册。  
**影响**：不能直接 hook libdexjni.so 的函数、不能通过模块名查地址。  
**替代**：
- 从 `/proc/PID/maps` 查找 ARM64 段（但 frida 读不到）
- 部分 ARM64 库有对应的 x86_64 版本（如 `libDexHelper-x86.so`）

### 2.4 启动速度太快

```
app start → 0.3s 内 PID 出现 → 文件操作已完成
```

**原因**：模拟器性能好，app 的 JNI_OnLoad/init_array 在 attach 前已经跑完。  
**影响**：hook fopen/openat 永远抓不到壳的解密文件操作。  
**常见误区**：`am start` 返回时 so 已经加载完成，timing 窗口极窄。

### 2.5 /proc/PID/mem 不可读

```
dd if=/proc/PID/mem → I/O error
```

**原因**：Android 15 的 SELinux 策略限制。pid 不属同一 UID 时无法读取。  
**替代**：用 frida 的 `Process.enumerateRanges()` + `readByteArray()` 在进程内读。

---

## 三、已知可用的调试手段

### 3.1 Frida attach（稳定）

```python
dev = frida.get_device_manager().add_remote_device('127.0.0.1:8080')
session = dev.attach(PID)
```

✅ 枚举模块、导出函数  
✅ 读进程内存（大部分区域）  
✅ hook libc 导出函数（fopen/openat/mmap/lseek/read）  
✅ hook libDexHelper-x86.so 的 JNI_OnLoad  

**注意事项**：
- PID 会变，每次重启后重新获取
- hook mmap 时大量事件（1089536 的反复出现是分配器，非解密）

### 3.2 直接 shell 命令

```bash
su -c 'cat /proc/PID/maps'  # 查看内存映射
su -c 'ls -la /proc/PID/fd/' # 查看打开的文件
su -c 'ls -la /data/app/.../oat/x86_64/base.odex' # OAT/DEX 文件
```

### 3.3 关键模块地址

```
libDexHelper-x86.so     @ 0x765a2ea51000  sz=0x151000 (1.3MB)
libdexfile.so           @ 0x765d6f600000  sz=0x40000
libart.so               @ 0x765aaadea000
base.odex               @ 0x765a30f45000  sz=15024128 (14.3MB)
libhoudini.so           @ 0x765a34691000  sz=0xd27000 (13.8MB)
```

---

## 四、爱加密 Ijiami 特点（基于此 app）

### 4.1 文件构成

| 文件 | 角色 |
|---|---|
| `lib/arm64-v8a/libdexjni.so` (1.8MB) | ARM64 解密引擎，11KB 代码 + 数据 |
| `lib/arm64-v8a/libDexHelper.so` (ARM64) | 壳的 ARM 原生库 |
| `lib/arm64-v8a/libDexHelper-x86.so` (1.2MB) | 壳的 x86_64 原生库（模拟器上直接加载） |
| `lib/armeabi-v7a/libSafeKBCrypter.so` | 邦 cle 的 so 加密，非爱加密切片 |
| `oat/x86_64/base.odex` (21KB) | ART 优化后的 odex（实际装载的内存映射 14.3MB） |

### 4.2 libdexjni.so 解密引擎

```
r-x: vaddr=0x0, sz=0x2ca0 (11KB 代码)
rw-: vaddr=0x12d08, sz=0x1bf3e0 (1.8MB 数据)

关键函数: 0x1184 (6KB, 1543 条 ARM64 指令)
算法: RC4 变体
密钥: 0x13068 (16 字节)
S-box: 无（初始化 0..255 标准 RC4 流程）
```

**函数 0x1184 流程**：
1. 初始化 S=[0..255]
2. KSA: 256 轮，key index 从 1 循环到 15 再回到 0
3. PRGA: 标准 RC4 keystream 生成
4. XOR 加密/解密数据
5. mmap 解密结果

**未解决的问题**：
- 密钥派生的代码不在 0x1184 中
- 0x13068 的 16 字节可能是初始状态，运行时被 init_array 覆写
- libdexjni.so 无 crypto 导入，密钥不可能在库内派生

### 4.3 libDexHelper-x86.so 特点

- 唯一导出：`JNI_OnLoad`
- 其余函数全静态不导出
- x86_64 原生，不需要 Houdini
- 可通过模块名 `Process.findModuleByName` 找到

---

## 五、调试策略总结

### 有效策略

| 手段 | 效果 |
|---|---|
| Frida attach + mmap hook | ✅ 大量数据，但需区分是否解密相关 |
| 读取 base.odex | ❌ 文件只有 21KB，内存映射 14.3MB |
| hook libDexHelper-x86.so JNI_OnLoad | ⚠️ 已调用完毕，hook 时已不在执行 |
| 全局 fopen/openat hook | ⚠️ 启动后 0.3 秒内完成，attach 太晚 |

### 失败策略

| 手段 | 失败原因 |
|---|---|
| Frida spawn | NullPointerException |
| Java.perform | Java not defined |
| /proc/PID/mem | SELinux I/O error |
| strace | 环境问题未出日志 |
| 纯静态 RC4 解密 | 密钥运行时派生 |

### 下一步建议

1. **换原厂 frida-server**（17.9.10）→ 可能解决 Java bridge 问题
2. **am start -D** → debug-wait 模式，attach 完再放行
3. **换 ARM64 真机** → 无 Houdini 转译，所有 ARM64 模块可见，Java bridge 可用
4. **查找 base.odex 实际 14.3MB 映射的来源** → 可能指向加密文件

---

## 六、常见报错及解决

| 报错 | 原因 | 解决 |
|---|---|---|
| `TypeError: not a function` | frida 首次 attach 时导出表未就绪 | 用 `enumerateExports` 替代 `findExportByName` |
| `Error: expected a pointer` | 把 NativePointer 传入泛型函数时类型错误 | 直接在 `Interceptor.attach()` 中使用地址 |
| `unable to write to process memory: No such process` | PID 过期 | 重新获取 PID |
| `Permission denied` (readByteArray) | 内存区域被保护（PAGE_NOACCESS） | 跳过，试其他区域 |
| spawn NullPointerException | nosuke 无 PackageManager 权限 | 换 frida-server 或放弃 spawn |

---

## 七、参考

- D:\逆向工具\ 工具清单
- D:\know base\本地so分析\06-实战-银行app壳对抗.md