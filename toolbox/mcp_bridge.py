"""外部 MCP 工具源桥接：把其他 MCP server 的工具编目进注册表。

接入 = 在 mcp.json（Claude .mcp.json 兼容格式）里加一段，重启会话生效：
  {"mcpServers": {"名字": {"command": "...", "args": [...]}}}   # stdio
  {"mcpServers": {"名字": {"url": "http://host/mcp"}}}          # streamable http
  stdio 源可加 "env": {"KEY": "..."} 追加环境变量（如桥接脚本的 PYTHONPATH）
  http 源可加 "headers": {"Authorization": "Bearer ${ENV_VAR}"} —— 值里的
  ${VAR} 从环境变量展开（令牌不落盘；引用的变量没设置则该源连接报错说明）

设计约束：
- 外部工具不进模型工具面：照旧 search_tools 发现、run_tool 执行，
  "上下文不随工具数增长"的承诺不破
- 每个源的连接在独立后台 task 里用 async with 一条链持有
  （绝不手动 __aenter__ —— 僵尸进程的老教训）
- 单个源连不上只记审计日志，不拖垮整个 server

捆绑呈现（bundle）：工具特别多的源（如 IDA 的 60+ 个）按"一个工具"编目——
模型只看到一张卡，op(操作名)+params(原生参数) 调用，操作按
常用只读/写操作/不常用 分组、每行带一句用途。66 张卡变 1 张，
搜索结果不再被同名异义的操作淹没。哪些源捆绑、操作怎么分组，见 _BUNDLES。
"""
import asyncio
import difflib
import json
import os
import re
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_CFG = Path(__file__).resolve().parent / "mcp.json"
TOOLBOX_ROOT = Path(__file__).resolve().parent
CONNECT_TIMEOUT_S = 8      # 死源快速放弃：本地/局域网连接 8s 足够，别拖垮整个会话启动


# 外部工具的副作用等级推断。gateway 的防兜圈逻辑靠它区分读/写：
# 非 readonly 的调用成功后会清空"已见结果"，重复调用就永远判不出来
# （IDA 的 get_bytes/analyze_function 曾因此被同参数反复调用十几轮）。
_WRITE_WORDS = ("patch", "put_", "set_", "rename", "define_", "undefine", "declare_",
                "delete", "remove", "upsert", "apply", "append_", "save", "write",
                "create", "update", "exec", "eval", "run", "open_", "select_",
                "infer_", "diff_before_after", "edit", "move", "kill")
_READ_PREFIXES = ("get_", "list_", "find", "search", "lookup", "analyze_", "decompile",
                  "disasm", "xref", "callee", "caller", "callgraph", "basic_blocks",
                  "func_", "entity_", "import", "export_", "int_convert", "survey",
                  "server_health", "stack_frame", "read_", "type_query", "type_inspect",
                  "trace_", "make_signature", "insn_query", "describe", "query", "show",
                  "fetch", "check", "stat", "info", "dump",
                  "assembly_", "grep")   # dnSpy 的程序集查询与跨类型 grep（均只读）


# 外部工具的使用约束（拼进工具卡 usage，模型第一次调用前就能看到）
_USAGE_NOTES = {
    "py_eval": "\n注意：代码在 IDA 进程内执行，没有 stdin（input() 会报 lost sys.stdin），只能 print 输出。"
               "数据直接用 ida_bytes.get_dword(ea)/get_byte(ea) 读，不要把工具结果手抄进代码。",
    "py_exec_file": "\n注意：脚本在 IDA 进程内执行，没有 stdin，只能 print 输出。",
}


# http 源自定义头里的 ${VAR} 引用（连 OAuth/PAT 保护的远端如 ctf2 用）
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def spec_headers(spec: dict, src: str) -> dict:
    """展开 spec.headers 值里的 ${环境变量}。令牌不进 mcp.json（ctf2 平台侧的家规），
    引用的变量没设置就直接抛错——比让请求裸奔出去吃一个模糊 401 强。"""
    raw = spec.get("headers") or {}
    if not isinstance(raw, dict):
        raise ValueError("headers 必须是 {名字: 值} 对象")
    out = {}
    for key, val in raw.items():
        missing = [m.group(1) for m in _ENV_REF_RE.finditer(str(val))
                   if not os.environ.get(m.group(1))]
        if missing:
            raise RuntimeError(f"环境变量 {', '.join(missing)} 未设置"
                               f"（mcp 源 {src!r} 的 headers.{key} 引用了它）")
        out[str(key)] = _ENV_REF_RE.sub(lambda m: os.environ.get(m.group(1), ""), str(val))
    return out


# ═══ 捆绑呈现：一个源收编成单个工具 ═══

_GROUPS = ("常用·只读", "写操作", "不常用")

# 源名 → 操作名 → (分组, 用途)。
# 未收录的操作自动落"不常用"并沿用远端英文描述；收录了但远端没有的自动剔除
# ——升级远端 MCP 不用同步改这里。
_IDA_OPS = {
    # 常用·只读 —— 逆向 90% 的动作都在这组
    "survey_binary":      ("常用·只读", "二进制总览：架构/段/入口/导入/字符串/热点函数，分析第一步（函数>10k 用 minimal）"),
    "decompile":          ("常用·只读", "反编译函数为 C 伪代码"),
    "disasm":             ("常用·只读", "反汇编，offset/max_instructions 分页"),
    "xrefs_to":           ("常用·只读", "查地址/函数被谁引用"),
    "xref_query":         ("常用·只读", "交叉引用查询：方向/类型/分页/去重"),
    "get_bytes":          ("常用·只读", "读内存原始字节（regions 批量）"),
    "get_string":         ("常用·只读", "读地址处字符串"),
    "get_int":            ("常用·只读", "按类型读整数：i8/u32/u64/le/be 批量"),
    "get_global_value":   ("常用·只读", "按名或地址读全局变量值"),
    "find":               ("常用·只读", "搜字符串/立即数/对地址的引用"),
    "find_regex":         ("常用·只读", "正则搜字符串（忽略大小写）"),
    "find_bytes":         ("常用·只读", "字节模式搜索，?? 通配"),
    "search_text":        ("常用·只读", "反汇编文本搜索（字面/正则，只扫代码段）"),
    "list_funcs":         ("常用·只读", "函数清单：filter 通配/正则 + 分页"),
    "lookup_funcs":       ("常用·只读", "按地址或名字定位函数"),
    "func_query":         ("常用·只读", "函数富查询：大小/类型/名字过滤"),
    "imports":            ("常用·只读", "导入表（offset/count 分页）"),
    "imports_query":      ("常用·只读", "导入富查询：按模块/名字过滤"),
    "entity_query":       ("常用·只读", "IDB 实体查询：functions/globals/imports/strings/names"),
    "py_eval":            ("常用·只读", "IDA 进程内跑 Python（万能兜底）。无 stdin、只能 print；读数据用 ida_bytes.get_dword 等，别把工具结果手抄进代码"),
    "server_health":      ("常用·只读", "探活：MCP 连接与当前 IDB 状态"),
    # 写操作 —— 改 IDB / 进程状态
    "rename":             ("写操作", "批量改名：函数/全局/局部/栈变量，dry_run 可选"),
    "set_comments":       ("写操作", "设注释（汇编+伪代码两视图同步）"),
    "append_comments":    ("写操作", "追加注释，同文自动去重"),
    "patch":              ("写操作", "写字节补丁（hex）"),
    "patch_asm":          ("写操作", "写汇编补丁"),
    "put_int":            ("写操作", "写整数到内存"),
    "declare_stack":      ("写操作", "从类型声明创建栈变量"),
    "declare_type":       ("写操作", "声明 C 类型进本地类型库"),
    "set_type":           ("写操作", "应用类型：函数签名/全局/局部/栈"),
    "enum_upsert":        ("写操作", "创建/扩展枚举（幂等，不破坏已有成员）"),
    "infer_types":        ("写操作", "在地址处推断并应用类型"),
    "diff_before_after":  ("写操作", "改名/改类型前后对照，确认改动生效"),
    "define_func":        ("写操作", "把地址定义成函数（可指定边界）"),
    "define_code":        ("写操作", "把字节转成代码指令"),
    "undefine":           ("写操作", "把代码/数据转回原始字节"),
    "idb_save":           ("写操作", "保存 IDB（可另存路径）"),
    "open_file":          ("写操作", "在新 IDA 实例打开文件并路由过去"),
    "select_instance":    ("写操作", "切换后续调用路由到的 IDA 实例"),
    # 不常用 —— 细粒度/辅助，参数结构报错会指明缺失字段
    "analyze_batch":      ("不常用", "多函数批量分析：自选板块（反汇编/xref/调用者…）"),
    "analyze_component":  ("不常用", "一组函数组件分析：摘要+内部调用图+共享数据"),
    "analyze_function":   ("不常用", "单函数紧凑全量分析（伪码/字符串/常量/调用关系）"),
    "basic_blocks":       ("不常用", "函数 CFG 基本块（分页）"),
    "callees":            ("不常用", "各函数的被调清单（去重）"),
    "callgraph":          ("不常用", "有界调用图：深度/节点/边数上限"),
    "export_funcs":       ("不常用", "导出函数为 json / C 头 / 原型"),
    "find_xref_signatures": ("不常用", "为引用目标地址的代码点生成特征码"),
    "func_profile":       ("不常用", "函数画像：调用者/被调/字符串/常量清单"),
    "insn_query":         ("不常用", "指令级查询：助记符/操作数过滤，可限定范围"),
    "int_convert":        ("不常用", "进制换算：hex/dec/bin/ascii"),
    "list_globals":       ("不常用", "全局变量清单（分页）"),
    "list_instances":     ("不常用", "列出已打开的 IDA 实例与端口"),
    "make_signature":     ("不常用", "从地址生成最短唯一特征码"),
    "make_signature_for_function": ("不常用", "函数入口特征码"),
    "make_signature_for_range": ("不常用", "地址范围特征码（不保证唯一）"),
    "py_exec_file":       ("不常用", "IDA 进程内跑脚本文件。无 stdin、只能 print"),
    "read_struct":        ("不常用", "按结构体字段读内存（自动探测类型）"),
    "search_structs":     ("不常用", "按名搜本地结构体"),
    "stack_frame":        ("不常用", "函数栈帧变量表"),
    "trace_data_flow":    ("不常用", "数据流多跳追踪：forward=流向 / backward=来源"),
    "type_apply_batch":   ("不常用", "批量应用类型改动"),
    "type_inspect":       ("不常用", "类型检查：大小/成员布局"),
    "type_query":         ("不常用", "类型目录查询：过滤/分页/关系"),
    "server_warmup":      ("不常用", "预热 IDA 子系统，降低首次调用延迟"),
    "xrefs_to_field":     ("不常用", "查结构体某字段的引用"),
}

# jadx（jadx-ai-mcp）：32 个操作三组。工具面 = jadx-gui 里打开的 APK，
# Java 层静态逆向（看业务逻辑/找加密入口），与 ida 卡正好分工：jadx 看 Java，ida 啃 so
_JADX_OPS = {
    # 常用·只读 —— 静态审计主线：结构 → 定位 → 源码
    "get_package_tree":   ("常用·只读", "包结构总览（按类数排序、标出疑似三方库）——分析第一步"),
    "get_main_activity_class": ("常用·只读", "从 Manifest 定位主 Activity 类"),
    "get_main_application_classes_names": ("常用·只读", "应用自有类名清单（自动排除三方库）"),
    "get_class_source":   ("常用·只读", "反编译指定类为 Java 源码"),
    "get_method_by_name": ("常用·只读", "取指定类中某方法的源码（可带签名精确定位）"),
    "get_methods_of_class": ("常用·只读", "列类的方法名清单"),
    "get_fields_of_class": ("常用·只读", "列类的字段名清单"),
    "get_smali_of_class": ("常用·只读", "取类的 smali 表示（Java 层看不清时对照字节码）"),
    "get_all_classes":    ("常用·只读", "全类清单（offset/count 分页）"),
    "search_classes_by_keyword": ("常用·只读", "关键词搜类：search_in 选 class/method/field/code 范围，package 限定包"),
    "search_method_by_name": ("常用·只读", "全局按方法名搜（定位加密/校验函数）"),
    "get_android_manifest": ("常用·只读", "读 AndroidManifest.xml 全文"),
    "get_manifest_component": ("常用·只读", "按类型取组件：activity/service/receiver/provider，可只看 exported"),
    "get_strings":        ("常用·只读", "读 strings.xml 字符串资源"),
    "get_resource_file":  ("常用·只读", "读指定资源文件内容"),
    "get_all_resource_file_names": ("常用·只读", "资源文件名清单"),
    "get_xrefs_to_class": ("常用·只读", "查谁引用了该类"),
    "get_xrefs_to_method": ("常用·只读", "查谁调用了该方法"),
    "get_xrefs_to_field": ("常用·只读", "查谁用了该字段"),
    # 写操作 —— 改的是 jadx 工程里的名字（帮助阅读），不动 APK 本体
    "rename_class":       ("写操作", "重命名类（去混淆）"),
    "rename_method":      ("写操作", "重命名方法（去混淆）"),
    "rename_field":       ("写操作", "重命名字段（去混淆）"),
    "rename_package":     ("写操作", "重命名整个包及其下所有类"),
    "rename_variable":    ("写操作", "重命名方法内变量"),
    "clear_cache":        ("写操作", "清反编译缓存（换 APK 或省内存时）"),
    # 不常用 —— 大批量/辅助
    "get_main_application_classes_code": ("不常用", "应用类源码整包拉取（分页，量大慎用）"),
    "get_cache_stats":    ("不常用", "反编译缓存命中率统计"),
    "fetch_current_class": ("不常用", "取 GUI 里当前选中的类（人在看屏时协作用）"),
    "get_selected_text":  ("不常用", "取 GUI 里当前选中的文本"),
    "debug_get_stack_frames": ("不常用", "调试中：当前调用栈（需 jadx 已附加调试）"),
    "debug_get_threads":  ("不常用", "调试中：线程清单"),
    "debug_get_variables": ("不常用", "调试中：当前变量值（进程挂起时）"),
}

# dnSpy（.NET 游戏逆向）：36 个操作三组。工具面 = dnSpy 里当前加载的程序集，
# Unity Mono 游戏的 Assembly-CSharp.dll 主战场；IL2CPP 游戏先 Il2CppDumper 出 dll 再加载
_DNSPY_OPS = {
    # 常用·只读 —— 游戏逆向主线：总览 → 搜 → 反编译
    "assembly_overview": ("常用·只读", "程序集总览：模块/版本/入口点/类型数/引用——分析第一步"),
    "list_loaded_assemblies": ("常用·只读", "dnSpy 当前已加载的二进制清单"),
    "search_types":       ("常用·只读", "按名字搜类型（'regex:' 前缀开正则）"),
    "search_methods":     ("常用·只读", "按名字搜方法（'regex:' 前缀开正则）"),
    "search_strings":     ("常用·只读", "搜字符串字面量（找提示语/密钥/URL）"),
    "search_constants":   ("常用·只读", "搜常量字段与枚举值（找魔数）"),
    "grep":               ("常用·只读", "跨类型/方法/字符串 grep"),
    "decompile_type":     ("常用·只读", "反编译整个类型为 C#（全部成员）"),
    "decompile_method":   ("常用·只读", "反编译单个方法，格式 'Namespace.Class::Method'"),
    "get_type_members":   ("常用·只读", "列类型成员清单（可按字段/属性/方法过滤）"),
    "get_type_hierarchy": ("常用·只读", "继承链与实现接口"),
    "get_fields":         ("常用·只读", "字段详情：类型/访问性/静态/const/默认值"),
    "get_method_signatures": ("常用·只读", "方法签名详情：参数/返回/属性/flags"),
    "get_enum_values":    ("常用·只读", "枚举全部命名值"),
    "get_xrefs_to":       ("常用·只读", "查谁引用了该方法/字段"),
    "get_callees":        ("常用·只读", "查该方法调用了谁"),
    # 写操作 —— 改的是内存里加载的程序集（rename 默认 dryRun 先预览）
    "rename_class":       ("写操作", "类改名（dryRun=true 默认先预览）"),
    "rename_method":      ("写操作", "方法改名（可部分匹配，dryRun 预览）"),
    "rename_namespace":   ("写操作", "命名空间整体改名"),
    "update_method_body": ("写操作", "C# 语句补丁方法体（dryRun=true 默认）"),
    "refresh_u_i":        ("写操作", "改动后刷新 dnSpy 文档标签生效"),
    # 不常用 —— 低层/辅助
    "get_method_il":      ("不常用", "方法原始 IL 指令（C# 层存疑时对照）"),
    "get_il_opcodes_formatted": ("不常用", "带行号的格式化 IL"),
    "get_method_body":    ("不常用", "方法体原始 IL 字节（特征码匹配用）"),
    "get_attributes":     ("不常用", "列特性（assembly/type/method/field）"),
    "get_method_attributes": ("不常用", "单方法特性（get_attributes 快捷方式）"),
    "get_properties":     ("不常用", "属性详情：getter/setter 签名"),
    "assembly_list_namespaces": ("不常用", "命名空间清单"),
    "assembly_list_types": ("不常用", "类型清单（regex 过滤 + 分页）"),
    "assembly_get_references": ("不常用", "程序集引用（DLL/NuGet）"),
    "get_global_namespaces": ("不常用", "无命名空间的全局类型"),
    "get_resources":      ("不常用", "嵌入资源清单"),
    "get_resource_data":  ("不常用", "读嵌入资源原始数据"),
    "get_metadata":       ("不常用", "PE 头/元数据版本/强名信息"),
    "decompile_assembly": ("不常用", "整程序集反编译（大程序集慢，慎用）"),
    "get_selected_node":  ("不常用", "GUI 当前选中节点（人在看屏时协作）"),
}

# x64dbg/x32dbg（动态调试）：23 个子系统三组。每个 op 的 params 形如
# {"action": {"action": "具体动作", ...}}（action 对象包一层）。桥接 = npm x64dbg-mcp-server
# (stdio) → 调试器内插件 REST :27042（32/64 位插件同端口，开哪个连哪个）。
# 调试器没有纯只读：所有 op 均按写处理（unknown），重复调用总是重跑取新值——动态语义正确。
_X64DBG_OPS = {
    # 常用·只读 —— 动态调试主循环：跑-停-看
    "x64dbg_debug":       ("常用·只读", "执行控制与状态：run/pause/step 单步三连/run_to_address/state"),
    "x64dbg_registers":   ("常用·只读", "寄存器读写：get_all/get_specific/get_flags/set（单步后看值）"),
    "x64dbg_disassembly": ("常用·只读", "反汇编 at_address/function/info + assemble 汇编回编"),
    "x64dbg_memory":      ("常用·只读", "内存 read/write/info/map/allocate/free/protect"),
    "x64dbg_breakpoints": ("常用·只读", "断点全套：软件/硬件/内存/条件/日志/批量配置"),
    "x64dbg_stack":       ("常用·只读", "栈：call_stack/原始读/指针/SEH 链/返回地址"),
    "x64dbg_modules":     ("常用·只读", "模块清单与 get_base/get_section（算 RVA 基址用）"),
    "x64dbg_search":      ("常用·只读", "字节模式(pattern, ?? 通配)/字符串/符号搜索"),
    "x64dbg_symbols":     ("常用·只读", "符号/标签/注释/书签：set_label 给关键函数起名"),
    "x64dbg_threads":     ("常用·只读", "线程清单/TEB/切换/挂起恢复"),
    "x64dbg_process":     ("常用·只读", "进程信息：basic/detailed/cmdline/elevated"),
    "x64dbg_command":     ("常用·只读", "x64dbg 原生命令/脚本/表达式求值——万能兜底"),
    # 写操作 —— 改调试对象/产出文件
    "x64dbg_patches":     ("写操作", "补丁管理：list/apply/restore/export 导出补丁模块"),
    "x64dbg_dumping":     ("写操作", "dump 模块/fix_iat 修导入表/PE 头与节/导入导出——脱壳收尾"),
    "x64dbg_antidebug":   ("写操作", "反调试对抗：读 PEB/TEB/DEP + hide_debugger 隐藏调试器"),
    "x64dbg_tracing":     ("写操作", "执行追踪：into/over/条件跑/日志落盘——跑得慢，慎用"),
    # 不常用 —— 辅助/查询
    "x64dbg_analysis":    ("不常用", "函数信息/xrefs/basic_blocks/源码定位"),
    "x64dbg_control_flow": ("不常用", "CFG/分支目标/循环检测 + 函数定义管理"),
    "x64dbg_database":    ("不常用", "常量表/错误码/结构体定义/模块内字符串搜索"),
    "x64dbg_address_convert": ("不常用", "VA↔文件偏移互转（dump 后对地址用）"),
    "x64dbg_exceptions":  ("不常用", "异常断点管理/Windows 异常码表/跳过异常"),
    "x64dbg_handles":     ("不常用", "句柄/TCP 连接/窗口/堆清单"),
    "x64dbg_watchdog":    ("不常用", "看门狗表达式是否已触发"),
}

# DASCTF CTF2 平台（agent 打题玩法）：http 源 + Agent 令牌鉴权（mcp.json 里
# Bearer ${CTF2_TOKEN}，令牌在 CTF2 网站的 Agent 控制台签发）。打题闭环：
# whoami → next_challenges → 读题 → 附件 → 起环境轮询就绪 → 解题 →
# submit_flag(confirmation:true) → log_note。成绩记 Agent 专属排行榜。
# 平台限流：120 读/分、30 写/分、同时最多 max_environments 个环境（默认 1）。
_CTF2_OPS = {
    # 常用·只读 —— 打题主线：找题 → 读题 → 看环境
    "ctf2_agent_whoami":  ("常用·只读", "Agent 身份/战绩/配额/允许的工具与规则——开打第一步"),
    "ctf2_agent_next_challenges": ("常用·只读", "推荐未解公开题（最简单、最多人解优先）"),
    "ctf2_list_practice_grounds": ("常用·只读", "公开练习场清单"),
    "ctf2_list_practice_challenges": ("常用·只读", "练习场内题目清单（category/difficulty/unsolved_only 过滤）"),
    "ctf2_get_practice_challenge": ("常用·只读", "读题面：描述/附件 file_id/has_container/套题子 flag"),
    "ctf2_get_attachment_url": ("常用·只读", "签附件下载链接（5 分钟有效，过期重签一个）"),
    "ctf2_get_environment": ("常用·只读", "环境状态：轮询到 access_ready 拿 access_url，看剩余时间"),
    # 写操作 —— 起环境/交 flag/记笔记
    "ctf2_start_challenge_environment": ("写操作", "启动/复用环境（幂等，起不来就反复调到就绪）"),
    "ctf2_extend_environment": ("写操作", "续期运行中的环境（与网页控制台同规则）"),
    "ctf2_user_delete_practice_id_challenges_challengeid_environment": ("写操作", "销毁环境腾配额（默认同时只能 1 个）"),
    "ctf2_submit_flag": ("写操作", "提交确认过的 flag（必须 confirmation:true；套题带 sub_flag_id）"),
    "ctf2_agent_log_note": ("写操作", "记解题笔记 kind=plan/finding/result（≤2000 字，构成解题回放）"),
    # 用户域工具（profile/赛事/ticket/社区等）不收录：Agent 令牌下调这些会
    # agent_scope_forbidden；若 tools/list 返回了会自动落"不常用"兜底
}

_BUNDLES = {"ida": _IDA_OPS, "jadx": _JADX_OPS, "dnspy": _DNSPY_OPS,
            "x64dbg": _X64DBG_OPS, "ctf2": _CTF2_OPS}

# 只读但结果随时间变化的操作 → 强制 unknown（按写处理）：readonly 成功会被
# 网关防兜圈记进"已见结果"，第二次同参调用直接判重复不执行——而轮询环境
# 就绪、重签过期附件链接恰恰需要重复同参调用真跑取新值
_OP_DANGER_OVERRIDE = {
    "ctf2": {"ctf2_get_environment": "unknown",
             "ctf2_get_attachment_url": "unknown"},
}

# 捆绑卡的门面文案（description/keywords 随源而异，操作表机制通用）
_BUNDLE_META = {
    "ida": {
        "description": ("IDA Pro 逆向分析——直接操作已在 IDA Pro 中打开的数据库，"
                        "无需本地文件路径。{n} 个操作的集合：常用只读"
                        "（反编译/交叉引用/读内存/搜索/py_eval）、写操作（改名/注释/补丁/类型）、"
                        "不常用（CFG/调用图/特征码）。按 op+params 调用，完整操作表见 usage。"),
        "keywords": ["IDA", "逆向", "反编译", "decompile", "反汇编", "disasm",
                     "交叉引用", "xref", "十六进制", "hex", "字节", "内存", "字符串",
                     "导入表", "函数", "伪代码", "补丁", "patch", "改名", "rename",
                     "CTF", "分析"],
    },
    "jadx": {
        "description": ("Jadx 安卓逆向——直接操作已在 jadx-gui 中打开的 APK，"
                        "无需本地文件路径。{n} 个操作的集合：常用只读"
                        "（包结构/类与方法源码/搜索/Manifest/资源/交叉引用）、"
                        "写操作（重命名去混淆）、不常用（批量源码/调试状态）。"
                        "Java 层看业务逻辑用这张卡，native 层（.so）用 ida 卡。"
                        "按 op+params 调用，完整操作表见 usage。"),
        "keywords": ["jadx", "安卓", "Android", "APK", "逆向", "反编译", "Java",
                     "smali", "dex", "Manifest", "混淆", "去混淆", "rename",
                     "交叉引用", "xref", "activity", "CTF", "分析"],
    },
    "dnspy": {
        "description": ("dnSpy .NET 逆向——直接操作已在 dnSpy 中加载的程序集，"
                        "无需本地文件路径。{n} 个操作的集合：常用只读"
                        "（程序集总览/搜类型方法字符串/反编译 C#/成员/继承/交叉引用）、"
                        "写操作（改名去混淆/C# 语句补丁方法体）、不常用（IL 层/特性/资源）。"
                        "Unity Mono 游戏改 Assembly-CSharp 用这张卡；IL2CPP 游戏先用"
                        " Il2CppDumper 出 dll 再拖进 dnSpy。按 op+params 调用，完整操作表见 usage。"),
        "keywords": ["dnSpy", "dnspy", ".NET", "dotnet", "C#", "游戏", "Unity",
                     "Mono", "IL2CPP", "IL", "逆向", "反编译", "程序集", "assembly",
                     "dll", "Assembly-CSharp", "外挂", "改名", "rename", "补丁",
                     "patch", "CTF", "分析"],
    },
    "x64dbg": {
        "description": ("x64dbg/x32dbg 动态调试——操作已打开的调试器（32/64 位插件"
                        "同端口 27042，开哪个连哪个，目标是什么位就开什么位）。"
                        "{n} 个子系统操作的集合：常用（执行控制/寄存器/内存/断点/"
                        "反汇编/栈/搜索/命令兜底）、写（补丁/dump 修 IAT/反调试对抗/追踪）、"
                        "不常用（CFG/句柄/异常）。参数形如 {{\\\"action\\\": {{\\\"action\\\": "
                        "\\\"read\\\", …}}}}（action 对象包一层）。动态脱壳、内存 dump、"
                        "追踪加密算法用这张卡；静态分析用 ida 卡。调试器无纯只读，"
                        "重复调用总是重跑取新值。按 op+params 调用，完整操作表见 usage。"),
        "keywords": ["x64dbg", "x32dbg", "调试器", "动态调试", "调试", "debug",
                     "断点", "breakpoint", "单步", "寄存器", "register", "内存",
                     "dump", "脱壳", "unpack", "OEP", "IAT", "补丁", "patch",
                     "反调试", "anti-debug", "追踪", "trace", "堆栈", "stack",
                     "ESP", "CTF", "分析", "游戏", "外挂"],
    },
    "ctf2": {
        "description": ("DASCTF CTF2 平台 Agent 打题——以注册 Agent 身份在专属排行榜打"
                        "公开练习题，成绩不影响号主本人。{n} 个操作的集合：常用只读"
                        "（身份配额/推荐题/读题/附件/环境状态）、写（起环境/续期/交 flag/"
                        "记笔记）。打题闭环：whoami → next_challenges → 读题 → 附件 → "
                        "起环境轮询到 access_ready → 解题 → submit_flag(必须 "
                        "confirmation:true) → log_note。限流 120 读/30 写每分钟。"
                        "报错看 structuredContent 的 code/retryable/hint。"
                        "按 op+params 调用，完整操作表见 usage。"),
        "keywords": ["CTF2", "DASCTF", "打题", "agent", "排行榜", "练习场",
                     "practice", "题目", "challenge", "环境", "environment",
                     "附件", "attachment", "flag", "提交", "submit", "笔记",
                     "note", "CTF", "解题", "whoami"],
    },
}


def is_bundled(source: str) -> bool:
    return source in _BUNDLES


def _param_hint(schema: dict, limit: int = 4) -> str:
    """操作参数提示：前几个参数名，可选带 ?。签名来源是远端 schema，随版本自动更新。"""
    props = (schema or {}).get("properties") or {}
    req = set((schema or {}).get("required") or [])
    return ", ".join(k + ("" if k in req else "?") for k in list(props)[:limit])


def _fallback_purpose(desc: str) -> str:
    """未收录操作的兜底用途：远端描述第一行，截 80 字。"""
    line = next((l.strip() for l in (desc or "").splitlines() if l.strip()), "")
    return (line[:80] + "…") if len(line) > 80 else (line or "(见 describe_tool)")


def bundle_tool(src: str, tools: list):
    """把一个源的裸工具列表收编成单个注册表 Tool。

    usage 是完整操作表（describe_tool / native 工具表可见）；
    op_danger 按操作判副作用，网关的防兜圈逻辑靠它区分读/写。
    """
    from registry import Tool
    curated = _BUNDLES[src]
    live = {t.name: t for t in tools}

    grouped: dict = {g: [] for g in _GROUPS}
    for name, (grp, purpose) in curated.items():
        if name in live:                       # 远端已移除的收录项自动剔除
            hint = _param_hint(getattr(live[name], "input_schema", None))
            grouped[grp].append(f"- {name}({hint}): {purpose}" if hint
                                else f"- {name}: {purpose}")
    for name in sorted(live):                  # 未收录的新操作垫在"不常用"末尾
        if name not in curated:
            grouped["不常用"].append(
                f"- {name}: {_fallback_purpose(getattr(live[name], 'description', ''))}")

    usage = (f"一次调用 = {{\"op\": 操作名, \"params\": {{该操作的原生参数}}}}，"
             f"参数名见各行括号（? = 可选）。\n\n"
             + "\n".join(f"【{g}】\n" + "\n".join(lines)
                         for g, lines in grouped.items() if lines))

    # enum 按分组排序（常用在前）：sig 折叠时露头的是高频操作，模型抄得准
    op_enum = ([n for g in _GROUPS for n, (grp, _) in curated.items()
                if grp == g and n in live]
               + sorted(n for n in live if n not in curated))

    meta = _BUNDLE_META.get(src) or {
        "description": "外部 MCP 源 {n} 个操作的集合，按 op+params 调用，完整操作表见 usage。",
        "keywords": [src, "MCP"],
    }
    return Tool(
        name=src,
        description=meta["description"].format(n=len(live)),
        category=f"mcp:{src}",
        danger="unknown",      # 混合体：读写按 op_danger 逐操作判定
        keywords=meta["keywords"],
        search_text=usage,
        example={"op": op_enum[0], "params": {}},   # 首个常用只读操作（真实环境=survey_binary 总览）
        schema={"type": "object", "required": ["op"],
                "properties": {
                    "op": {"type": "string", "enum": op_enum,
                           "description": "操作名，完整分组表见 usage"},
                    "params": {"type": "object", "description": "该操作的原生参数，各操作的参数名见 usage 括号提示"}},
                "additionalProperties": False},   # 参数放错位置（如 addr 顶层）会被指出
        timeout=300,
        requires=[],
        source=f"mcp:{src}",
        path=None,
        op_danger={n: _OP_DANGER_OVERRIDE.get(src, {}).get(n) or infer_danger(t)
                   for n, t in live.items()})


def unpack_bundle(params: dict, known: set) -> tuple:
    """拆捆绑调用的 {op, params}。op 缺失/未知/params 非法时抛 ValueError（带相近建议），
    server 转成 validation_error 让模型自纠错。"""
    op = str((params or {}).get("op") or "").strip()
    if not op:
        raise ValueError("缺少 op（操作名）。完整操作表见该工具的 usage / describe_tool。")
    if op not in known:
        near = difflib.get_close_matches(op, known, n=3, cutoff=0.5)
        hint = f"最相近: {', '.join(near)}。" if near else ""
        raise ValueError(f"未知操作 {op!r}。{hint}完整操作表见该工具的 usage / describe_tool。")
    inner = params.get("params")
    if inner is None:
        inner = {}
    if not isinstance(inner, dict):
        raise ValueError("params 必须是对象（该操作的原生参数）。")
    return op, inner


def infer_danger(tool) -> str:
    """MCP annotations 优先；没有就按工具名推断。拿不准的仍是 unknown（按写处理，保守）。"""
    ann = getattr(tool, "annotations", None)
    if ann is not None:
        if getattr(ann, "read_only_hint", None) is True:
            return "readonly"
        if getattr(ann, "destructive_hint", None) is True:
            return "intrusive"
    name = tool.name.lower()
    if any(w in name for w in _WRITE_WORDS):
        return "unknown"
    if name.startswith(_READ_PREFIXES):
        return "readonly"
    return "unknown"


class McpBridge:
    def __init__(self):
        self._started = False
        self._sources = {}   # name -> {"session", "task", "tools": [raw Tool]}

    async def ensure(self) -> None:
        """幂等懒加载：首个工具调用时连一次。"""
        if self._started:
            return
        self._started = True
        import audit
        cfg_path = Path(os.environ.get("AGENT_MCP_JSON", DEFAULT_CFG))
        audit.log_event("search", query="(mcp_bridge)",
                        results={"cfg": str(cfg_path), "exists": cfg_path.exists()})
        if not cfg_path.exists():
            return
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            audit.log_event("validation_error", tool="mcp_bridge",
                            reason="bad_config", detail=str(e))
            return
        for name, spec in (cfg.get("mcpServers") or {}).items():
            try:
                await self._connect(name, spec)
                audit.log_event("search", query="(mcp_bridge)",
                                results={name: len(self._sources[name]["tools"])})
            except Exception as e:
                audit.log_event("validation_error", tool=f"mcp:{name}",
                                reason="connect_failed", detail=str(e))

    async def _connect(self, name: str, spec: dict) -> None:
        http_client = None            # 自建的带鉴权头 client：SDK 不代管，退出时要自己关
        if "url" in spec:
            from mcp.client.streamable_http import streamable_http_client
            headers = spec_headers(spec, name)
            if headers:
                # 本 venv 的 SDK 无 headers 形参：自建 client 塞进去（保持 MCP 推荐超时）
                from mcp.shared._httpx_utils import create_mcp_http_client
                http_client = create_mcp_http_client(headers=headers)
                cm = streamable_http_client(spec["url"], http_client=http_client)
            else:
                cm = streamable_http_client(spec["url"])
        else:
            command = Path(str(spec["command"])).expanduser()
            if not command.is_absolute():
                command = (TOOLBOX_ROOT / command).resolve()
            args = []
            for arg in spec.get("args", []):
                item = Path(str(arg)).expanduser()
                if not item.is_absolute() and ("/" in str(item) or "\\" in str(item)):
                    item = (TOOLBOX_ROOT / item).resolve()
                args.append(str(item))
            params = StdioServerParameters(
                command=str(command), args=args,
                # env 整体替换，必须继承；spec.env 追加/覆盖（如给桥接脚本设 PYTHONPATH）
                env={**os.environ, "PYTHONIOENCODING": "utf-8", **spec.get("env", {})})
            cm = stdio_client(params)

        ready = asyncio.Event()
        holder = {}

        async def _hold() -> None:
            try:
                async with cm as streams:
                    read, write = streams[0], streams[1]
                    async with ClientSession(read, write) as s:
                        await asyncio.wait_for(s.initialize(), timeout=30)
                        holder["session"] = s
                        holder["tools"] = list((await s.list_tools()).tools)
                        ready.set()
                        await asyncio.Event().wait()   # 保活；task 被 cancel 时在同一任务内正常退栈
            finally:
                if http_client is not None:
                    await http_client.aclose()

        task = asyncio.create_task(_hold())
        try:
            await asyncio.wait_for(ready.wait(), timeout=CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            task.cancel()
            raise RuntimeError(f"mcp source '{name}' 连接超时")
        self._sources[name] = {"session": holder["session"],
                               "tools": holder["tools"], "task": task}

    def catalog(self) -> dict:
        """外部工具 → 注册表条目（registry.Tool 同形）。category 归入 mcp:<源名>。
        捆绑源（_BUNDLES）整源一张卡；其余按裸工具逐个编目。"""
        from registry import Tool
        out = {}
        for src, info in self._sources.items():
            if is_bundled(src):
                out[src] = bundle_tool(src, info["tools"])
                continue
            for t in info["tools"]:
                out[t.name] = Tool(
                    name=t.name,
                    description=(t.description or "").strip()[:60],
                    category=f"mcp:{src}",
                    danger=infer_danger(t),
                    keywords=[],
                    # 约束放最前：网关拼工具表时 usage 只取前 600 字
                    search_text=(_USAGE_NOTES.get(t.name, "").lstrip("\n") + "\n"
                                 + (t.description or "")).strip(),
                    example={},
                    schema=getattr(t, "input_schema", None)
                           or {"type": "object", "properties": {}},
                    timeout=300,
                    requires=[],
                    source=f"mcp:{src}",
                    path=None)
        return out

    def op_names(self, source: str) -> set:
        """捆绑源当前可用的操作名集合（分发时校验 op 用）。"""
        info = self._sources.get(source)
        return {t.name for t in info["tools"]} if info else set()

    async def call(self, source: str, tool_name: str, params: dict) -> str:
        info = self._sources.get(source)
        if info is None:
            return json.dumps({"status": "error",
                               "detail": f"工具源 {source} 不可用（连接失败或已断开）"})
        result = await info["session"].call_tool(tool_name, params)
        parts = [c.text for c in result.content if hasattr(c, "text")]
        text = "\n".join(parts) or "(空输出)"
        if getattr(result, "is_error", None) or getattr(result, "isError", None):
            raise RemoteToolError(text)
        err = remote_error(text)
        if err:
            raise RemoteToolError(text, err)
        return text


class RemoteToolError(Exception):
    """外部工具执行失败：raw 是原始输出（照常落盘），reason 是一行失败原因。"""

    def __init__(self, raw: str, reason: str = ""):
        super().__init__(reason or raw[:200])
        self.raw = raw
        self.reason = reason or last_error_line(raw)


def last_error_line(text: str) -> str:
    """Traceback 的有效信息在最后一行（如 RuntimeError: lost sys.stdin）。"""
    lines = [l.strip() for l in text.replace("\\n", "\n").splitlines() if l.strip()]
    for l in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b", l):
            return l[:200]
    return (lines[-1] if lines else text)[:200]


def remote_error(text: str) -> str:
    """很多 MCP server 失败也回 isError=false，把错误塞在 JSON 字段里。识别两种常见形态：
    {"error": "..."}（非空）与 {"stderr": "Traceback ..."}（py_eval 类）。返回失败原因，成功返回空串。"""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ""
    items = [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, dict)]
    if not items:
        return ""
    for v in items:
        stderr = v.get("stderr")
        if isinstance(stderr, str) and "Traceback" in stderr:
            return last_error_line(stderr)
    # 批量查询里个别条目带 error 不算整体失败：全部条目都报错才算
    errs = [str(v["error"]) for v in items if v.get("error")]
    if errs and len(errs) == len(items):
        return errs[0][:200]
    return ""
