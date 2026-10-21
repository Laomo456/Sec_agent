# IDA 9.4 — 自内 CLI 功能配合（急速）

> 场景：不开 GUI、不等全量分析，用 CLI 脚本模式 `idat -A -S` 直接跑目标函数。
> 这是「IDA 只当展示/验证引擎」的落地版 —— 定位靠 lief/capstone，IDA 只反编译你要的函数。
> 版本：IDA Professional 9.4.260714，路径 `D:\IDA Pro 9.4.260714`

---

## 一、核心命令

### 1. 批处理/脚本模式（主力）
```bash
"D:\IDA Pro 9.4.260714\idat.exe" -A -S"script.py out_dir" <目标文件>
```
- `-A`：自动模式，不弹对话框
- `-S"script.py"`：启动后执行 IDAPython 脚本
- `-S` 后面的参数会被当作 `sys.argv` 传给脚本
- `idat.exe`（文本界面）比 `ida.exe` 资源占用小，适合批处理

### 2. 等价简化
```bash
ida -B input   # 等价 ida -c -A -Sanalysis.idc input
```
但推荐直接 `-A -S` 自定义脚本，可控更高。

### 3. 帮助
```bash
"D:\IDA Pro 9.4.260714\idat.exe" -h
```

---

## 二、IDAPython 9.x API 变更（重要）

IDA 9.x 重构了大量 API，老脚本直接搬过来不兼容。以下是实测变更：

| 功能 | 老 API（≤8.x） | IDA 9.x 新 API |
| --- | --- | --- |
| 处理器名 | `idaapi.get_inf_structure().get_proc_name()` | `idc.get_inf_attr(idc.INF_PROCNAME)` |
| 段数量 | `idc.get_segm_qty()` | `ida_segment.get_segm_qty()` |
| 取段 | `idc.getnseg(i)` | `ida_segment.getnseg(i)` |
| 段名 | `idc.get_segm_name(seg)` | `ida_segment.get_segm_name(seg)` |
| 段头/尾 | `seg.startEA / endEA` | `seg.start_ea / seg.end_ea` |
| 段权限 | `idc.SEGPERM_R/W/X` | `seg.perm` 直接位比较，无 idc 常量 |
| 函数名 | `idaapi.get_func_name(ea)` | `ida_funcs.get_func_name(ea)` |
| 遍历符号 | `idautils.Functions()` + `idc.get_func_name(ea)` | `idautils.Names()` 更稳 |
| 反汇编行 | `idc.generate_disasm_line(ea, 0)` | `ida_lines.generate_disasm_line(ea, 0)` |
| 字符串判断 | `idc.is_strlit(flags)` | `ida_bytes.is_strlit(flags)` |
| 读字符串 | `idc.get_strlit_contents(ea)` | `ida_bytes.get_strlit_contents(ea)` |
| 代码判断 | `idc.is_code(flags)` | `ida_bytes.is_code(flags)` |
| 字节属性 | `idc.get_full_flags(ea)` | `ida_bytes.get_full_flags(ea)` |
| 下一指令 | `idc.next_head(ea, end)` | `ida_bytes.next_head(ea, end)` |
| 自动等待 | `ida_auto.wait_for_idle()` | Windows 上可能不好用，用 `ida_auto.auto_wait()` |
| 导入 idautils | 顶层自动导入 | 需手动 `import idautils` |
| `idc.BADADDR` | ✅ 可用 | ✅ 仍然可用 |
| `idc.INF_MIN_EA` | ✅ 可用 | ✅ 仍然可用 |

### 模块导入（9.x 必需）
```python
# 每个都要显式 import
import idc
import ida_auto
import ida_funcs
import ida_segment
import ida_bytes
import ida_lines
import idautils  # 手动 import！
import ida_hexrays  # F5 反编译
```

---

## 三、最小可用模板（9.x 兼容）

以下是经过实际验证、能在 9.4 跑通的脚本模板：

```python
# 9.4 最小工作脚本
import sys, os
import idc, ida_auto, ida_funcs, ida_segment, ida_bytes, ida_lines
import idautils

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(idc.get_input_file_path())
    
    # 1. 等待自动分析
    ida_auto.auto_wait()
    
    lines = []
    lines.append(f"# IDA 分析报告\n")
    
    # 2. 基本信息
    proc = idc.get_inf_attr(idc.INF_PROCNAME)
    seg_qty = ida_segment.get_segm_qty()
    lines.append(f"- 处理器: {proc}\n")
    lines.append(f"- 段数: {seg_qty}\n")
    
    # 3. 段表
    for i in range(seg_qty):
        seg = ida_segment.getnseg(i)
        if seg:
            name = ida_segment.get_segm_name(seg)
            start = seg.start_ea
            end = seg.end_ea
            perm = ""
            if seg.perm & 4: perm += "R"   # 无 idc.SEGPERM_R
            if seg.perm & 2: perm += "W"
            if seg.perm & 1: perm += "X"
            lines.append(f"  {name} {hex(start)} {end-start}B {perm}\n")
    
    # 4. 遍历命名符号（替代 idautils.Functions）
    java_exports = []
    for ea, name in idautils.Names():
        if "Java_" in name:
            java_exports.append((ea, name))
    
    lines.append(f"\nJNI 导出: {len(java_exports)}\n")
    
    # 5. 反编译指定函数（需 ida_hexrays）
    # import ida_hexrays
    # cf = ida_hexrays.decompile(target_ea)
    # lines.append(str(cf))
    
    # 6. 退出
    with open(os.path.join(out_path, "report.txt"), "w") as f:
        f.writelines(lines)
    
    idc.qexit(0)

if __name__ == "__main__":
    main()
```

---

## 四、验证用例（9.4 实测）

```python
# cli_test.py —— 跑完退
import sys, idc, ida_auto, idautils
ida_auto.auto_wait()
n = len(list(idautils.Names()))
with open(sys.argv[1] + "/test.txt", "w") as f:
    f.write(f"names={n}")
idc.qexit(0)
```
```bash
"D:\IDA Pro 9.4.260714\idat.exe" -A -S"cli_test.py C:\Users\20751\Desktop\异环\reports" calc.exe
```

---

## 五、IDA 9.4 idalib 模块（Python 脚本不启动 IDA）

`idapro` Python 包可以在不启动 IDA 的情况下调用分析引擎：

```python
from idapro import open_database, init_code

# 头部模式：不完整分析，只读元数据
db = open_database(r"target.so", init_code=init_code.HEAD)
print(db.arch, list(db.functions.keys())[:10])
db.close()
```

与 `idat -A -S` 的区别：

| 方式 | 适用场景 | 缺点 |
| --- | --- | --- |
| `idat -A -S` | 完整分析 + 反编译 | 每次重启 IDA，加载慢 |
| `idalib HEAD 模式` | 秒读元数据、导出符号 | 不能反编译/F5 |

---

## 六、配合工具链（先定位，IDA 只做最后验证）

| 步骤 | 工具 | 目的 |
| :--- | :--- | :--- |
| 1. 定位关键函数/字符串 | lief / capstone / strings / yakit_grep | 秒级找到目标偏移 |
| 2. 跑真实逻辑（可选） | unidbg | 绕过 OLLVM 假代码 |
| 3. IDA 脚本反编译目标 | `idat -A -S"script.py out"` | 只反编译指定函数，不等全量 |
| 4. AI 读伪代码 | 导出文本给 AI | 不用人啃反汇编 |

**核心**：`idat -A -S` 只分析你指定的函数，跳过全量 —— IDA 加载慢不再是瓶颈。

---

## 七、注意事项

1. **`-S` 脚本里必须 `idc.qexit(0)` 退出**，否则 IDA 不自动关。
2. 脚本模式**不加载普通插件**，纯 IDAPython 可用。
3. 大 SO（如 237MB 的 `libUnreal.so`）脚本模式仍会做全量分析，指定 start_ea/end_ea 可加快。
4. 环境变量：`IDA_HOME` = `D:\IDA Pro 9.4.260714`，PATH 已含。
5. **Windows 上 `idautils.Functions()` 可能失效**，用 `idautils.Names()` 代替更稳。
6. **`ida_auto.auto_wait()` 在 Windows 上等不够久时**，可在脚本开头加 `import time; time.sleep(2)`。

---

## 八、★实战：反控制流平坦化（Hex-Rays 直接破 OLLVM）

> 结论：**IDA 9.4 的 Hex-Rays（F5）能反编译"控制流平坦化(OLLVM)"函数为可读 C**，不必先手写反混淆工具。
> 实测：某商业保护 native 全函数为 `stp x29,x30,[sp,#-0x60]! → mov wState,#imm → cmp/b.ge` 状态机；`idat -A -S` **直接吐可读 C**（状态变量流转清晰）。

### 为什么行
- 平坦化只是把 CFG 拆成"`state` 变量 + 分发器"，**仍是合法 AArch64** → Hex-Rays 的 data-flow 能重建。
- 前提：**从"正确的指令边界"起反汇编**（否则错位成"看似合理实则假"的指令）。

### 脚本要点
```python
import idc, ida_hexrays
idc.auto_wait()                     # 等自动分析完
cf = ida_hexrays.decompile(ea)      # ea = 目标函数起始 vaddr
f.write(str(cf)); idc.qexit(0)      # 必须 qexit
```
- 未重定位的 `.so` → **直接用文件里的 vaddr**（如 `0x25ae0`），**不要加 imagebase**。
- **坑**：`idc.get_imagebase()` / `ida_idaapi.get_imagebase()` 在 **9.4 不存在**，别用。

### 产出怎么用
1. 读函数真实逻辑（参数/分支/**被调子函数偏移**）。
2. 拿子函数偏移 → `unidbg` 的 `Module.callFunction(emulator, offset, args)` **逐个跑**（见 SO 库·动态调试）。
3. 配合"字符串获取函数"→ **黑盒变白盒**。

### 定位函数入口的坑
- 从**已知函数头**（`stp x29,x30` / `paciasp`）起反汇编；**不要**用"任意 `addr-0x18`"当起点 → 会错位。
- 函数列表用 `idautils.Names()`；`.init_array` 的 ctor 从**重定位**(`R_AARCH64_RELATIVE` 的 addend)恢复（文件里是 0）。

---

## 九、相关
- headless 长时间连接：见 `gui后台启动连接使用(适合长时间)/IDA94_GUI后台与headless连接.md`
- idalib HEAD 模式：见 `IDA94_自内CLI脚本模式.md` 同目录（本文件）
- SO 分析方法论：见 `D:\know base\本地so分析\`
- SO 本地调试（unidbg）：见 `D:\know base\本地so调试\`