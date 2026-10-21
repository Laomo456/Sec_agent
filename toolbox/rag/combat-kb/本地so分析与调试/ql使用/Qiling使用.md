# 方法(动态调试)：Qiling 本地执行 + 补环境

> 定位：用 Qiling（Python，基于 QEMU/Unicorn）在 Windows 本地加载 `.so` 执行。
> 与 unidbg 互补：Qiling **无需补 Java 回调**，适合自包含算法 / 无 JNI 依赖的场景。
> 判据驱动，**不举具体实例**。
> 安装位置：`D:\逆向工具\qiling\`。
>
> ⚠ **边界**：本文件只讲**补环境方法论**（重定位 / TLS / pthread_once / init）。
> **Qiling 工具安装与用法细节写在工具文档另处**（见 `D:\环境变量\逆向工具.md`），本库不重复。

---

## 一、原理与选型

```
Qiling = QEMU(系统调用/内存) + Unicorn(CPU) + 你的补环境
加载 ELF → 映射 → 应用重定位 → 跑 init_array → 调用目标函数 / hook
```

| 维度 | Qiling | unidbg |
| --- | --- | --- |
| 语言 | Python | Java |
| JNI / Java 回调 | ❌ 不适合 | ✅ 原生（MyJni）|
| 自包含算法（白盒 / 自实现加密）| ✅ 首选 | ✅ |
| 补环境难度 | 中（需补重定位 / TLS / init）| 中（补 JNI 回调）|
| 系统调用 | ✅ 较完整 | ⚠ 需 hook |
| 适合场景 | 无 JNI、需真实 syscall 的库 | 带 JNI 注册 + Java 交互的库 |

**原则**：`JNI_OnLoad` + `RegisterNatives` 的库优先 unidbg；纯算法 / 无 JNI 入口的库优先 Qiling。

---

## 二、环境（工具目录已就绪）

- **源码版**：`D:\逆向工具\qiling\qiling-master`（**pip 上的 qiling 是空壳，不能用**）
- **rootfs**：`D:\逆向工具\qiling\rootfs\rootfs-master\arm64_android`
- **工作目录**：`D:\逆向工具\qiling\work\`（放待分析 SO）

用法（通过 `sys.path` 引入源码版）：
```python
import sys
sys.path.insert(0, r"D:\逆向工具\qiling\qiling-master")
from qiling import Qiling
```

> **路径必须纯英文**：中文路径会触发 `UnicodeEncodeError: latin-1`。待分析 SO 先复制到 `D:\逆向工具\qiling\work\`。
> 如需重装：`pip install D:\逆向工具\qiling\qiling-master` + 依赖 `python-registry gevent multiprocess questionary windows-curses`。

---

## 三、核心流程

```
1. 静态定目标（偏移 / 函数范围）
2. Qiling 加载 SO（映射 + 打桩导入）
3. 【关键】手动应用重定位
4. 补环境（TLS/金丝雀、pthread_once、cxa_guard）
5. 跑 init_array（构造函数）
6. 调用目标函数 / hook
```

---

## 四、手动重定位（**最关键的一步**）

**Qiling 不会应用许多 SO 的 `.rela.dyn` 重定位。** 加载后 `.data.rel.ro` 里的指针会停在 **0 基址**（如 `0xa0978` 而非 `base+0xa0978`），导致：
- PLT 跳到裸地址崩溃
- 构造函数跳到错误地址
- 内部全局表（如 OpenSSL）全是垃圾

**解法**：加载后自己遍历重定位表补齐：

```python
from elftools.elf.elffile import ELFFile
elf = ELFFile(open(so, "rb"))
dynsym = elf.get_section_by_name('.dynsym')
R_RELATIVE, R_GLOB_DAT, R_JUMP_SLOT, R_ABS64 = 1027, 1025, 1026, 257

for sec in elf.iter_sections():
    if sec.name not in ('.rela.dyn', '.rela.plt', '.rel.plt'):
        continue
    for rel in sec.iter_relocations():
        off, typ, add = rel['r_offset'], rel['r_info_type'], rel.get('r_addend', 0)
        if typ == R_RELATIVE:                      # 相对重定位
            write(ql.base + off, pack64(ql.base + add))
        else:
            sym = dynsym.get_symbol(rel['r_info_sym'])
            if sym['st_shndx'] == 'SHN_UNDEF':      # 导入 -> 桩
                write(ql.base + off, pack64(stub_for(sym.name)))
            else:                                    # 库内符号 -> 真实地址
                write(ql.base + off, pack64(ql.base + sym['st_value'] + add))
```

**判据**：抽查若干 `.rela.dyn` 的 `R_AARCH64_RELATIVE` 项，加载后其值应为 `base + addend`。

---

## 五、补环境清单

| 项 | 不补的后果 | 补法 |
| --- | --- | --- |
| **TLS / 栈金丝雀** | `mrs xN,tpidr_el0; ldr xM,[xN,#0x28]` 读崩 | `ql.uc.reg_write(UC_ARM64_REG_TPIDR_EL0, tls_addr)`；在 `tls+0x28` 放金丝雀 |
| **pthread_once** | init 回调不执行 → 全局未初始化 | 桩写成 trampoline，**检查 once 标志**（见下）|
| **`__cxa_guard_acquire/release`** | C++ 静态局部未初始化 | acquire 首次返 1；release 置标志位 |
| **内存分区** | TLS / 合成对象 / malloc 堆重叠被覆盖 | 各自分配**独立**的映射区 |
| **内部日志函数** | 缺 log sink → 读空指针崩 | hook 目标日志函数：打印格式串后 `pc = x30` 跳过 |
| **导入函数** | PLT 跳崩 | 每个导入填桩（`ret`）+ hook 分发（见第八节）|

### pthread_once trampoline（关键）

`pthread_once(once, init)` 桩若**每次都跑 init**，而 init 内部又调 `pthread_once` → **无限递归 → 栈崩**。
必须**先查 once 标志**，只跑一次：

```asm
ldr  w9, [x0]              ; once 当前值
cbnz w9, skip              ; 已初始化 → 跳过
mov  w9, #1
str  w9, [x0]              ; 标记已初始化
stp  x29,x30,[sp,#-0x10]!
mov  x29, sp
blr  x1                    ; 调用 init 回调
ldp  x29,x30,[sp],#0x10
skip:
mov  w0, #0                ; pthread_once 成功返回 0
ret
```

---

## 六、调用任意函数的骨架

```python
# 1. 加载
ql = Qiling([so_path], rootfs=rootfs)          # so_path 必须英文
base = ql.loader.load_address

# 2. 手动重定位（第四节）+ 导入打桩
apply_relocations(ql, so_path)                 # 每个导入填桩 + hook

# 3. 补环境（第五节）
setup_tls(ql); patch_pthread_once(ql); ...

# 4. 跑 init_array
for f in read_init_array(so_path):
    try: emu_call(ql, f, [])
    except Exception: pass                      # 个别构造函数失败可跳过

# 5. 调用目标函数
def emu_call(ql, vaddr, args):
    for i in range(31): setattr(ql.arch.regs, f'x{i}', 0)
    for i, a in enumerate(args): setattr(ql.arch.regs, f'x{i}', a)
    ql.arch.regs.sp = STACK_TOP
    ql.arch.regs.x30 = SENTINEL                 # 返回哨兵
    ql.emu_start(base + vaddr, SENTINEL)

emu_call(ql, 0x<目标偏移>, [arg0, arg1, ...])
```

- 导入桩：每个导入分配一个可执行桩地址，桩体 `ret`（`0xd65f03c0`），用 `ql.hook_address(cb, stub)` 分发（`cb` 里实现语义 / 返回默认值，必要时 `pc = x30` 直接返回）。
- `pthread_once` 的桩体换成第五节 trampoline（**不要**再挂通用 hook）。
- 返回哨兵：映射一页全 0，`emu_start(begin, SENTINEL)` 在函数 `ret` 到哨兵时结束。

---

## 七、Hook

```python
# 指令级 / 地址级
ql.hook_address(lambda q: print("hit", hex(q.arch.regs.x0)), base + 0x<偏移>)

# 指令流追踪（看调用树）
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
def on_code(q, addr, size):
    for i in md.disasm(q.mem.read(addr, 4), addr):
        if i.mnemonic == "bl":
            print(f"bl -> {int(i.op_str[1:],16):#x}")
ql.hook_code(on_code)
```

- 屏蔽内部日志：hook 日志函数，打印 `ql.mem.string(x1)` 后 `q.arch.regs.pc = q.arch.regs.x30`。
- mock 外部依赖：hook 对应桩，按需填返回值 / 写内存。

---

## 八、常见坑

| 坑 | 现象 | 对策 |
| --- | --- | --- |
| 不重定位 | PLT 跳裸地址 / 构造函数跑飞 | 手动应用 `.rela.dyn`（第四节）|
| 中文路径 | `UnicodeEncodeError: latin-1` | SO 复制到纯英文路径 |
| `tpidr_el0=0` | 金丝雀读崩 | 设 TLS 块 |
| pthread_once 递归 | 栈损坏 / 早退 | 带 once 标志的 trampoline |
| `__cxa_guard` 返 0 | C++ 静态未初始化 | acquire 首次返 1 |
| 内存重叠 | 对象被 malloc 覆盖 | 分区独立映射 |
| 缺 log sink | 日志里读空指针崩 | hook 屏蔽日志函数 |
| 导入桩返回错值 | 分支走错 / 失败 | 桩按语义返回（如 `pthread_once` 返 0）|
| pip 的 qiling | `ImportError` / 空壳 | 用**源码版** |
| 单构造函数崩 | 整个流程中断 | 逐个 `try/except` 跳过 |

---

## 九、可信度判据（闭环）

- **加载即映射**：`ql.loader.load_address` 正常，`.rela.dyn` 抽查 = `base+addend`。
- **构造函数多数成功**：`init_array` 中大部分执行无异常。
- **目标函数返回**：跑到返回哨兵。
- **闭环**：`变换 A 的结果能被逆向变换 B 还原`（如 `encode→decode` 回原文）= 环境 / 参数补对了。

---

> 与 unidbg 的分工见 `unidbg本地hook.md` 第七节；要测什么见 `目的(关键分析)/目的.md`。
