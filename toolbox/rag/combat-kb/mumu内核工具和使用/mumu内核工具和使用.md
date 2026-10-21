# MuMu 内核工具和使用手册

> 更新: 2026-08-15 16:00 (全面回填: 8.4 达成状态 + 8.8 第三版 + 8.9 攻防实验) | 设备: 127.0.0.1:16416 / 127.0.0.1:7555 (MuMu Android 15 测试机, 重启后端口可变)
> 内核: 6.1.90-perf+ x86_64 (伪 aarch64) | root: KernelSU

## 1. 工具清单 (全部可用)

| 工具 | 路径 | 用途 |
| --- | --- | --- |
| insmod / rmmod / lsmod / modprobe | /system/bin | 内核模块装卸 (无签名+FORCE_LOAD) |
| dmesg | /system/bin | 内核日志 (KernelSU 拦截日志可见) |
| sysctl | /system/bin | 内核参数读写 |
| strace | /system/bin | ptrace 观测 + **syscall 注入** |
| taskset | /system/bin | CPU 亲和性绑定 |
| renice / chrt | /system/bin | 调度优先级 |
| nsenter / unshare | toybox | 命名空间 |
| busybox | /system/xbin | 通用工具 |
| bcc | /system/bin | LLVM bitcode 编译器 (非BPF工具) |
| frida (nsv11) | /data/local/tmp/x/nsv11:31337 | 用户态分析 + **内核联动执行器** |

## 2. 观测接口 (内核视角看进程)

```bash
# 实时 syscall (当前线程正在内核干什么)
cat /proc/<PID>/syscall
# 例: "281 0x40 ..." = epoll_pwait

# 反调试自检观测 (TracerPid!)
cat /proc/<PID>/status | grep -E "TracerPid|Seccomp|voluntary"

# seccomp 状态验证
grep Seccomp /proc/<PID>/status   # 2 = 过滤器已装

# 内核配置
zcat /proc/config.gz | grep CONFIG_

# 模块/符号
lsmod; head /proc/kallsyms
```

## 3. 内核干预 (已验证)

```bash
# 关全局 ASLR (影响所有新进程)
sysctl -w kernel.randomize_va_space=0

# 改日志级别
sysctl -w kernel.printk=4

# 清页缓存 (释放内存)
sync; echo 3 > /proc/sys/vm/drop_caches

# 进程绑 CPU (调度干预)
taskset -p 2 <PID>          # 只允许 CPU1
taskset -p <PID>            # 查看当前 affinity

# 提优先级
renice -n -10 -p <PID>
chrt -p <PID>               # 查看调度策略
```

## 4. strace 观测 + syscall 注入 (核心玩法)

```bash
# 观测 syscall 流
strace -f -p <PID> -e trace=openat,connect,socket

# ★ 注入返回值 (伪内核 hook!)
strace -f -p <PID> -e inject=getpid:retval=999999 -e trace=getpid
# 实测: 快手 getpid 全返回 999999, 进程无感继续跑

# 防自杀注入 (信号类 syscall 吞掉)
strace -f -p <PID> -e inject=tgkill:retval=0 -e trace=tgkill

# 注入错误码 / 延迟
strace -f -p <PID> -e inject=openat:retval=-1
strace -f -p <PID> -e inject=read:delay_exit=1000
```

要点:
- attach 模式对**已运行进程**生效 (实测快手 266 线程 OK)
- 快手读 TracerPid 检测 ptrace 但**不杀** (对比火影 TP 自杀)
- strace 就是 ptrace——TP 能查 TracerPid, 但 strace 短时挂载实际可用

## 5. frida × 内核联动 (★ 突破)

原理: frida agent 只作"触发器", 内核执行真正功能 (无 hook 特征残留)

```bash
# 重启 server 后首次 attach 成功率高 (通道经验)
adb shell su -c 'killall nsv11; nohup /data/local/tmp/x/nsv11 -D &'
frida -H 127.0.0.1:31337 -p <PID> -e "..."
```

```js
// ★ eBPF map 创建 (实测 fd=17 成功!)
var sy = new NativeFunction(l.findExportByName('syscall'),'long',['long','long','pointer','long']);
var a = Memory.alloc(128);
a.writeU32(2); a.add(4).writeU32(4); a.add(8).writeU32(4); a.add(12).writeU32(16);
var fd = sy(321, 0, a, 128);   // bpf(BPF_MAP_CREATE) → fd=17

// seccomp 安装 (同通道 prctl)
// BPF_PROG_LOAD 加载 kprobe 程序 (下一步)
```

坑:
- NativeFunction 'pointer' 参数传 0 必须 ptr(0)
- frida 写文件到 /data/local/tmp 会 Permission denied (app uid) — 用 adb 验证代替
- 长脚本加载易超时 → 用短内联 -e

## 6. 内核模块路线 (等备份机编译)

```bash
# 内核状态已确认支持:
# CONFIG_MODULES=y, 无 MODULE_SIG, FORCE_LOAD=y, FORCE_UNLOAD=y
# tainted=4096 (已有 vboxsf/vaddress 未签名模块)

insmod /data/local/tmp/hook.ko    # 装载即可用
```

目标: syscall 拦截模块 (仿 KernelSU: newfstatat/faccessat/execve hook)

## 7. 失败教训记录

1. frida attach 超时 ≠ 被检测 — 快手对 ptrace 无防御仍超时 → agent 注入环节问题
2. attach 前重启 nsv11 恢复成功率
3. spawn 火影崩溃 (SIGSEGV executing non-executable memory) = TP 自杀模式
4. 首次 attach + 极短脚本 = 成功率最高组合
5. /proc/PID/mem 读取 0x4000... 高位地址返回全零 (dd 大 offset 问题待查)

## 8. ★ eBPF 系统级注入 (NDK 原生路线, 已全通 2026-08-15)

### 8.1 工具链 (本机 Windows)
- NDK: D:\android-ndk-r27d (解压后 sysroot 需完整! 用 tar 解压, powershell 会丢文件)
- 编译器: toolchains\llvm\prebuilt\windows-x86_64\bin\x86_64-linux-android35-clang.cmd
- 编译: `x86_64-linux-android35-clang.cmd -O2 -static -o out in.c`
- 源码: C:\Users\20751\Desktop\test\bpf\ (bpf_inj.c=最小加载, bpf_stack.c=抓栈, bpf_diag.c=诊断)
- 设备端: /data/local/tmp/bpf_inj, /data/local/tmp/bpf_stack

### 8.2 已打通链路
```
原生 ELF (root 直跑, 无 zygote seccomp)
  → BPF_MAP_CREATE (STACK_TRACE map) ✓
  → BPF_PROG_LOAD (KPROBE prog, bpf_get_stackid) ✓
  → 动态 kprobe 注册: echo "p:kprobes/xxx do_sys_openat2" > /sys/kernel/tracing/kprobe_events
  → perf_event_open(PERF_TYPE_TRACEPOINT, kprobe_id) + SET_BPF + ENABLE ✓
  → bpf_get_stackid 抓栈 → BPF_MAP_GET_NEXT_KEY 迭代 dump ✓
  → 符号化: sysctl -w kernel.kptr_restrict=0 后 kallsyms 对照
实测: do_sys_openat2 栈帧 0xffffffffa7f51d31 = do_sys_openat2+0x1 ✓
```

### 8.3 关键坑 (全部踩过)
1. **app_process(Java) 调 bpf 被杀** — zygote 基线 seccomp 拦 bpf + Os.syscall 在 Android 15 已被移除 (NoSuchMethodException) → 必须原生 ELF
2. **helper EACCES** — map 引用必须 BPF_LD_MAP_FD 伪指令 (0x18), 不能 mov rN,fd
3. **ld_map 占 2 指令槽** — insn_cnt 要 +1, 数组补占位元素
4. **tracing_on=0** gate 所有事件 — 先 echo 1 > /sys/kernel/tracing/tracing_on
5. **CONFIG_PERF_TRACEPOINTS 未编译** — syscall tracepoint 的 perf 事件永不触发 → 用动态 kprobe 事件 (CONFIG_KPROBE_EVENTS=y, 已验证 id 可读)
6. **kptr_restrict** 隐藏地址 — sysctl -w kernel.kptr_restrict=0
7. perf_event read 计数为 0 但栈能抓到 (读取语义问题, 不影响栈)
8. KPROBES_ON_FTRACE 限制: kprobe 栈回溯只有 1 帧 (bpf_get_stackid)

### 8.4 当前限制与扩展 (2026-08-15 已部分达成)
- 内核栈 1 帧 (ftrace kprobe 限制)
- ✅ 已达成: BPF_F_USER_STACK (1<<8) 用户栈 → 见 8.5 (注意: 栈顶帧总是 libc wrapper, 看不到 ARM 调用者)
- 待试: bpf_get_stack (helper 67) 多帧
- ✅ 已达成: 火影 pid 定向 → prog 内 helper 14 过滤 (perf_event_open 的 pid 参数对 kprobe 无效, 见 8.5)

### 8.5 ★ 完整验证 (2026-08-15 第二轮)
- **BPF_F_USER_STACK 用户栈**: ✅ 抓到 (0x26353a = __openat+0xa, llvm-nm 符号化精确命中)
- **PID 过滤**: ✅ 用 bpf_get_current_pid_tgid (helper 14) 在 prog 内过滤, 实测只抓目标 pid (火影空闲 0 次=过滤正确; 自触发 fopen 抓到)
- **perf_event_open 的 pid 参数对 kprobe 无效** → 必须 prog 内 helper 14 过滤 (kprobe 事件全局触发)
- 跳转偏移坑: JNE off 计算 = 目标insn - 当前insn - 1 (off=5 而非 7, verifier 报 "jump out of range")
- 符号化工具: NDK llvm-nm.exe -n bpf_stack (静态二进制保留符号)
- 火影空闲态不调 openat (0 次/10s) — 挂载后需触发活动或换高频函数
- 用法: /data/local/tmp/bpf_stack kprobe user <pid|0=自己>  (3 参)
- kprobe 可挂任意函数: do_sys_openat2 / __x64_sys_* / 任意内核符号 (kallsyms 可读)

### 8.6 ★ 系统级 hook 全通 (2026-08-15 第三轮)
- **多 CPU 挂载**: pid=-1 时 perf_event_open 必须逐 CPU (cpu=0..N); 一个 prog 只能挂一个 perf event (EEXIST) → **每 CPU 独立 BPF_PROG_LOAD 副本**
- **★ BUG 教训: target_pid=-1 全进程模式** — imm=-1 在 prog 里是无符号 0xFFFFFFFF, 所有 pid 都 != 0xFFFFFFFF → 全被过滤 → 0 次。修复: target<0 时生成无过滤指令序列 (跳过 helper 14 比较)
- **系统级 openat2 调用者分布**: 全进程采样抓到 9 条路径 (0x449edb 静态区 / 0x718beedb350a ART / 0x7f6b/0x7f2d/0x7cf0/0x792d/0x7e7d 各进程 / 0x717182fb950a 系统区)
- **待改进**: stack map 只存栈不关联 pid — 需加 pid map (map[stackid]=pid) 才能知道栈属于谁
- **诊断方法**: kprobe ftrace 侧触发 (tracefs enable+count) 但 perf 侧 0 次 → 查 events/kprobes/<name>/enable; 事件触发验证: echo 1 > events/.../enable + grep trace
- 火影空闲态 syscall 极少 (线程全阻塞) — 采样要配合活动触发或挂高频入口

### 8.7 火影加载 so 画像 (49657, 2026-08-15)
- **TP 家族**: libtersafe.so (主检测) + libtprt.so + libtgpa.so + libcgsdk.so + libcgsdk-loader.so + libCrashSight.so + libMGCProfiler.so + libqimei/libtdmqimei (指纹)
- **引擎**: libunity.so + libil2cpp.so + libmain.so + libxlua.so + lib_burst_generated.so
- **业务**: MSDKPIX 全家桶 / libgcloud/libgcloudcore / libGCloudVoice / libfmod / libmmkv / libRoosterNN / libPxKit3 / libHttpDnsPlugin 等
- 全部 arm64 (Android 15 转译执行), 无 memfd 隐藏 so

### 8.8 bpf_stack PID 过滤修复 (2026-08-15)
- **BUG**: 源码 insns[6] `JNE off=7` 越界 → verifier "jump out of range from insn 6 to 14" → PROG_LOAD EINVAL
- **修复**: `off=5` (目标 = 当前insn + 1 + off = 6+1+5 = 12, 跳过 get_stackid 段), 见 8.5 第 3 行
- **源码**: C:\Users\20751\Desktop\test\bpf\bpf_stack.c (已改) → bpf_stack_fixed 编译产物
- **部署**: 已覆盖设备 /data/local/tmp/bpf_stack (旧版备份 bpf_stack.bak_off7), 验证 PID 定向采样 OK (PROG_LOAD fd=4, 挂 9 CPU)
- **2026-08-15 第三版**: 完整版 (pid 过滤 + 无过滤分支 + 多 CPU + 采样秒数参数) 已覆盖设备; 用后必清理 kprobe_events + tracing_on
- 用法: `/data/local/tmp/bpf_stack <fn> user <pid|-1全进程> <秒>`

### 8.9 ★ 系统级攻防实验 (2026-08-15 第四轮, 火影 com.tencent.KiHan)
**安全通道结论 (全部实测)**:
- ✅ **strace 100% 安全** (多次 attach 不死; TracerPid 非 0 容忍)
- ✅ **eBPF 完整挂载安全** (稳定前台 20s 不死; 之前"eBPF 杀火影"是 monkey 切前台变量混淆!)
- ✅ kprobe 注册单独 / tracing_on=1 单独 / attach+读内存 全部安全
- ❌ **frida inline hook (Interceptor 改指令) = TP 检测触发点** (26551 hook libc openat 期间死, 日志 "Process terminated")
- → **TP 检测"代码被修改" (完整性校验), 不是检测 ptrace**

**hook 方式矩阵**:
| 方式 | 结果 |
|---|---|
| strace (ptrace) | ✅ 不死 |
| frida attach 无 hook | ✅ 不死 (348 模块枚举) |
| frida + inline hook libc | ❌ 死 |
| GOT 替换 (改数据段) | 无效: libc 内部不走 GOT; ARM 转译代码调 libc 也不走 GOT (libc r-- 段 0 个 openat 槽) |
| libhoudini hook | 无目标: 161 导出全混淆 (s_0000xx/x.N/y.N) + NativeBridgeItf/ExtendItf/ibt_get_expire_date/.datadiv_decode232603832353925792 |

**TP 行为画像**:
- 事件驱动: 平时极安静, 有特征 (inline hook 改指令) 才深度检查/自杀
- **只在游戏前台活跃时运行**; 后台被 App Freezer 冻结 (90s 0 syscall, 线程 24 vs 前台 154)
- 读 /proc/self/maps 低频 (Thread-97, 2次/几分钟) — 扫 frida memfd/gum 特征
- 1439次/60s tgkill(SIG_0) = **Unity 启动线程池探测** (非 TP! 二次 attach 0 次证实)
- 27028 读 /proc/self/stat + meminfo = 性能监控 (非检测)

**闪退真相**: MSDKPolicyActivity (及 .Substitute0) 弹窗时主线程 SIGSEGV (pc=0x320, #01 dalvik-LinearAlloc) — 模拟器 ART/转译兼容 bug, 偶发, 非检测非工具 (tombstone_22; 26551 稳定 1h+ 对照)

**eBPF 用户栈 1 帧限制**: 栈顶帧总是 libc wrapper (0x8750a = libc.so openat) — 看不到 ARM 调用者; 地址对照 maps 需数值比较 (PC python)