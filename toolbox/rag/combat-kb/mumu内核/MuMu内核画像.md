# MuMu Android 15 内核画像 (测试机)

> 更新: 2026-08-15 | 设备: 127.0.0.1:7555 (MuMu 实例1「测试机」, 重启后端口可变: 16416→7555)

## 1. 基础信息

| 项 | 值 |
| --- | --- |
| 内核版本 | Linux 6.1.90-perf+ #14 SMP PREEMPT_DYNAMIC |
| 编译时间 | 2026-07-30 03:32:47 UTC |
| 编译器 | Android clang 19.0.1 (r536225, +pgo +bolt +lto +mlgo) |
| 真实架构 | **x86_64** (cpuinfo: AMD Ryzen 7 H 255 透传) |
| uname 伪装 | machine 字段报 **aarch64** (MuMu 修改,骗 ARM 检测) |
| 虚拟化 | clocksource=kvm-clock, 组件 vboxsf/vboxguest (VirtualBox) |
| 内核 taint | 4096 (TAINT_UNSIGNED_MODULE — 已有未签名模块) |

## 2. 内核配置 (关键项)

| 配置 | 值 | 意义 |
| --- | --- | --- |
| CONFIG_MODULES | y | 模块机制开启 |
| CONFIG_MODULE_SIG | **未设置** | **无签名验证** |
| CONFIG_MODULE_FORCE_LOAD | y | vermagic 不匹配也强载 |
| CONFIG_MODULE_FORCE_UNLOAD | y | 可强制卸载 |
| CONFIG_MODVERSIONS | y | 符号版本校验(可绕过) |
| CONFIG_BPF_SYSCALL | y + BPF_JIT_ALWAYS_ON | eBPF 可用 |
| CONFIG_KALLSYMS_ALL | y | 内核全符号 |
| CONFIG_KPROBES | y + KPROBES_ON_FTRACE | 内核探针 |
| CONFIG_SECCOMP + FILTER | y | seccomp 全功能(含 user_notif) |
| CONFIG_USER_NS | y | 用户命名空间 |
| CONFIG_IKCONFIG_PROC | y | /proc/config.gz 可读 |

## 3. 已加载模块

| 模块 | 大小 | 说明 |
| --- | --- | --- |
| vboxsf | 73728 | VirtualBox 共享文件夹 |
| vboxguest | 380928 | VirtualBox guest |
| vaddress | 20480, **65 引用** | MuMu 地址转换(ARM 转译内核支持) |

## 4. ARM 转译层 (native bridge)

- 引导: libnb.so (19KB, NativeBridgeItf 实现)
- **核心: houdini64 + libhoudini.so (9.7MB)** — Intel Houdini ARM 转译
- /system/lib64/arm64 = ARM 库视角; /system/bin/houdini64 (x86_64 ELF)
- 结论: 与 Android 12 相同的 houdini 方案, ARM svc → x86 syscall → 可被 seccomp 拦

## 5. 伪装层 (对 TP 的影响)

| 项 | 值 | 备注 |
| --- | --- | --- |
| ro.build.fingerprint | Redmi/munch:12/SKQ1.211006.001 | 假指纹(Android 12) |
| ro.build.version.release | 15 | 真版本 |
| cpuinfo | 真身 x86_64 (AMD) / **ARM 视图 0x4e NVIDIA 伪装** | **2026-08-15 已伪造高通 0x51** (见反检测方法论附录 A6) |
| uname | aarch64 假 | 已伪装 |

## 6. 实测: 内核干预已验证

| 操作 | 结果 |
| --- | --- |
| sysctl -w kernel.printk=4 | ✅ 写入成功 |
| sysctl -w kernel.randomize_va_space=0 | ✅ ASLR 全局关闭成功 |
| echo 3 > /proc/sys/vm/drop_caches | ✅ 清缓存成功 (释放 ~500MB) |
| insmod 通路 | ✅ 命令可用, 只差 .ko |
| lsmod/dmesg/sysctl | ✅ 全部可用 |
| KernelSU | ✅ 内核级 root (dmesg 可见 su 拦截日志) |

## 7. Android 15 vs Android 12 (为何 15 爽)

| 维度 | Android 12 (旧) | Android 15 (现) |
| --- | --- | --- |
| 转译 | houdini, frida hook libc 必死 | 同 houdini, 但**内核边界拦截突破**(seccomp/strace inject) |
| 内核 | 未探索 | 无签名+FORCE_LOAD 模块, BPF 全开, 全符号 |
| strace attach | 未试 | ✅ 快手 266 线程稳定, 注入返回值成功 |
| frida×内核联动 | 无 | ✅ bpf() 直通 (BPF_MAP_CREATE fd=17) |
| 内核干预 | 无 | ✅ ASLR 关/drop_caches/taskset 全成功 |
| KernelSU | 无 | ✅ 内核级 root, syscall hook 实证 |
| **eBPF 系统级注入** | 无 | ✅ **kprobe 挂载 + PID 过滤 + 用户/内核栈 + 符号化全通** (NDK 原生 ELF, 2026-08-15) |
| **环境检测对抗** | 无 | ✅ cpuinfo 伪造生效 (TP 读到高通值) |

## 8. 结论与下一步

- **内核开放度极高**: 无签名 + FORCE_LOAD 模块自由装卸; eBPF 全开
- **eBPF 已全链打通** (NDK r27d 本机编译, C:\Users\20751\Desktop\test\bpf\bpf_stack.c): kprobe 任意函数 + PID 过滤 + 用户栈 + 符号化 (详见 mumu内核工具和使用.md 第 8 节)
- **自制 .ko 是终极手段** (未签名 + FORCE_LOAD): 编译链已就绪 (NDK 可编 .ko? 需内核头; 或备份机 root@192.168.253.135)
- 现在可做: eBPF 观测/注入、strace inject 防自杀、cpuinfo 伪造、时序窗口 hook
- 编译链: **NDK r27d 已装 (D:\android-ndk-r27d)** — 静态 ELF 本机编译直接推设备
