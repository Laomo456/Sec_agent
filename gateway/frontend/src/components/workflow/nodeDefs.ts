/** 节点注册表：类型 → 外观、默认配置、出口 handle、输出变量。
 *  与 gateway/workflow.py 的 EXECUTORS 一一对应；新增节点类型两边各加一处。 */
import type { WfNode } from "../../types";

export interface HandleDef { id: string; label?: string; tone?: "ok" | "err" | "muted" }
export interface VarDef { name: string; type: string; desc?: string }

export interface NodeDef {
  type: string;
  label: string;
  desc: string;
  category: "基础" | "模型" | "逻辑" | "转换" | "工具";
  color: string;
  icon: string;               // 24x24 path
  container?: boolean;        // iteration / loop：可容纳子节点
  hidden?: boolean;           // 不在节点库里出现（容器起点等）
  noInput?: boolean;
  noOutput?: boolean;
  defaults: () => Record<string, any>;
  outputs: (data: Record<string, any>) => HandleDef[];
  vars: (data: Record<string, any>) => VarDef[];
  summary?: (data: Record<string, any>) => string;
}

const out = (): HandleDef[] => [{ id: "out" }];
const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 7)}`;

/** 失败走异常分支时，额外多一个 fail 出口 */
export function handlesOf(node: Pick<WfNode, "type" | "data">): HandleDef[] {
  const def = NODE_DEFS[node.type];
  if (!def || def.noOutput) return [];
  const hs = def.outputs(node.data || {});
  if (node.data?.on_error === "branch") hs.push({ id: "fail", label: "异常", tone: "err" });
  return hs;
}

const I = {
  start: "M8 5v14l11-7z",
  end: "M6 6h12v12H6z",
  answer: "M4 5h16v11H8l-4 4zM8 10h8M8 13h5",
  llm: "M12 3l2.2 5.3L20 9l-4.4 3.8L17 18.5 12 15.6 7 18.5l1.4-5.7L4 9l5.8-.7z",
  classifier: "M4 6h7M4 12h5M4 18h7M15 6l5 6-5 6",
  extractor: "M4 4h16v6H4zM7 14h10M9 18h6M12 10v4",
  if_else: "M6 3v6a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v6M6 21v-6M18 3l-3 3 3 3",
  code: "M8 7l-5 5 5 5M16 7l5 5-5 5M13 5l-2 14",
  template: "M5 4h14v4H5zM5 11h8M5 15h11M5 19h6",
  http: "M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z",
  tool: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z",
  agent: "M12 2a4 4 0 0 1 4 4v1h1a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3h1V6a4 4 0 0 1 4-4zM9 13h.01M15 13h.01M9 17h6",
  aggregator: "M4 5l6 7-6 7M20 12H10",
  assigner: "M4 8h10M4 16h10M17 5v14M14 8l3-3 3 3",
  list_op: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  iteration: "M4 7h11a4 4 0 0 1 0 8H8M8 11l-4 4 4 4",
  loop: "M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3",
  note: "M5 3h10l4 4v14H5zM15 3v4h4",
};

export const NODE_DEFS: Record<string, NodeDef> = {
  start: {
    type: "start", label: "开始", desc: "工作流入口，定义输入变量", category: "基础",
    color: "#8f7cff", icon: I.start, noInput: true,
    defaults: () => ({ variables: [{ name: "query", label: "用户输入", type: "paragraph", required: true }] }),
    outputs: out,
    vars: (d) => (d.variables || []).map((v: any) => ({ name: v.name,
      type: v.type === "boolean" ? "boolean" : v.type === "number" ? "number" : "string", desc: v.label })),
    summary: (d) => (d.variables || []).map((v: any) => v.name).join(" · ") || "无输入",
  },
  end: {
    type: "end", label: "结束", desc: "输出工作流结果", category: "基础",
    color: "#f472b6", icon: I.end, noOutput: true,
    defaults: () => ({ outputs: [{ name: "result", value: "" }] }),
    outputs: () => [], vars: () => [],
    summary: (d) => (d.outputs || []).map((o: any) => o.name).join(" · "),
  },
  answer: {
    type: "answer", label: "直接回复", desc: "向用户输出一段文本，可流式、可多次", category: "基础",
    color: "#fb7185", icon: I.answer,
    defaults: () => ({ text: "" }),
    outputs: out, vars: () => [{ name: "text", type: "string" }],
    summary: (d) => d.text || "未配置",
  },
  llm: {
    type: "llm", label: "LLM", desc: "调用大模型生成文本或 JSON", category: "模型",
    color: "#60a5fa", icon: I.llm,
    defaults: () => ({ system: "", prompt: "", temperature: 0.7, output: "text", memory: false, profile: "" }),
    outputs: out,
    vars: (d) => [{ name: "text", type: "string", desc: "生成内容" },
      ...(d.output === "json" ? [{ name: "json", type: "object", desc: "解析后的 JSON" }] : []),
      { name: "usage", type: "object" }],
    summary: (d) => d.prompt || "未配置提示词",
  },
  classifier: {
    type: "classifier", label: "问题分类器", desc: "让模型把输入归类，按类别走不同分支", category: "模型",
    color: "#34d399", icon: I.classifier,
    defaults: () => ({ input: "{{sys.query}}", classes: [{ id: uid("c"), name: "类别 1" }, { id: uid("c"), name: "类别 2" }], instruction: "", profile: "" }),
    outputs: (d) => (d.classes || []).map((c: any) => ({ id: c.id, label: c.name })),
    vars: () => [{ name: "class_name", type: "string" }, { name: "class_id", type: "string" }, { name: "reason", type: "string" }],
  },
  extractor: {
    type: "extractor", label: "参数提取器", desc: "从自然语言里提取结构化参数", category: "模型",
    color: "#2dd4bf", icon: I.extractor,
    defaults: () => ({ input: "{{sys.query}}", params: [{ name: "keyword", type: "string", description: "", required: true }], instruction: "", profile: "" }),
    outputs: out,
    vars: (d) => [...(d.params || []).map((p: any) => ({ name: p.name, type: p.type || "string", desc: p.description })),
      { name: "__is_success", type: "boolean" }, { name: "__reason", type: "string" }],
    summary: (d) => (d.params || []).map((p: any) => p.name).join(" · "),
  },
  agent: {
    type: "agent", label: "智能体", desc: "自主调用工具完成任务（完整 agent 循环）", category: "模型",
    color: "#a78bfa", icon: I.agent,
    defaults: () => ({ task: "", system: "", max_iterations: 10, toolset_id: "all", profile: "" }),
    outputs: out,
    vars: () => [{ name: "text", type: "string", desc: "最终回答" }, { name: "tool_calls", type: "array" }, { name: "iterations", type: "number" }],
    summary: (d) => d.task || "未配置任务",
  },
  if_else: {
    type: "if_else", label: "条件分支", desc: "IF / ELIF / ELSE，按条件走不同分支", category: "逻辑",
    color: "#fbbf24", icon: I.if_else,
    defaults: () => ({ cases: [{ id: uid("case"), logic: "and", conditions: [{ var: "", op: "contains", value: "" }] }] }),
    outputs: (d) => [...(d.cases || []).map((c: any, i: number) => ({ id: c.id, label: i === 0 ? "IF" : `ELIF ${i}` })),
      { id: "else", label: "ELSE", tone: "muted" as const }],
    vars: () => [{ name: "result", type: "string" }],
  },
  iteration: {
    type: "iteration", label: "迭代", desc: "对列表的每一项执行一组节点", category: "逻辑",
    color: "#f59e0b", icon: I.iteration, container: true,
    defaults: () => ({ input: "", output_var: "", parallel: false, parallel_limit: 4, max_items: 100 }),
    outputs: out,
    vars: () => [{ name: "output", type: "array", desc: "每项结果组成的列表" }, { name: "count", type: "number" },
      { name: "item", type: "any", desc: "（容器内）当前项" }, { name: "index", type: "number", desc: "（容器内）当前序号" }],
  },
  loop: {
    type: "loop", label: "循环", desc: "重复执行一组节点直到满足条件", category: "逻辑",
    color: "#f97316", icon: I.loop, container: true,
    defaults: () => ({ max_loops: 5, output_var: "", break_case: { logic: "and", conditions: [] } }),
    outputs: out,
    vars: () => [{ name: "rounds", type: "number" }, { name: "output", type: "any" },
      { name: "index", type: "number", desc: "（容器内）当前轮次" }],
  },
  aggregator: {
    type: "aggregator", label: "变量聚合", desc: "多条分支汇合，取第一个有值的变量", category: "逻辑",
    color: "#94a3b8", icon: I.aggregator,
    defaults: () => ({ variables: [] }),
    outputs: out, vars: () => [{ name: "output", type: "any" }],
  },
  assigner: {
    type: "assigner", label: "变量赋值", desc: "写入会话变量（env），可累加/追加", category: "逻辑",
    color: "#c084fc", icon: I.assigner,
    defaults: () => ({ items: [{ name: "counter", mode: "set", value: "" }] }),
    outputs: out, vars: () => [],
    summary: (d) => (d.items || []).map((i: any) => `env.${i.name}`).join(" · "),
  },
  code: {
    type: "code", label: "代码执行", desc: "运行 Python：main(**inputs) 返回 dict", category: "转换",
    color: "#38bdf8", icon: I.code,
    defaults: () => ({
      inputs: [{ name: "arg1", value: "" }],
      code: "def main(arg1):\n    return {\n        \"result\": str(arg1),\n    }\n",
      outputs: [{ name: "result", type: "string" }], timeout: 30,
    }),
    outputs: out,
    vars: (d) => (d.outputs || []).map((o: any) => ({ name: o.name, type: o.type || "any" })),
  },
  template: {
    type: "template", label: "模板转换", desc: "用 {{变量}} 拼接文本", category: "转换",
    color: "#818cf8", icon: I.template,
    defaults: () => ({ template: "" }),
    outputs: out, vars: () => [{ name: "output", type: "string" }],
    summary: (d) => d.template || "未配置",
  },
  list_op: {
    type: "list_op", label: "列表操作", desc: "拆分 / 过滤 / 排序 / 切片 / 去重 / 合并", category: "转换",
    color: "#22d3ee", icon: I.list_op,
    defaults: () => ({ input: "", op: "split", sep: "\\n", value: "", key: "", start: 0, end: null, desc: false }),
    outputs: out,
    vars: () => [{ name: "output", type: "array" }, { name: "count", type: "number" }, { name: "first", type: "any" }, { name: "last", type: "any" }],
  },
  http: {
    type: "http", label: "HTTP 请求", desc: "调用外部 API", category: "工具",
    color: "#4ade80", icon: I.http,
    defaults: () => ({ method: "GET", url: "", headers: [], params: [], body: "", body_type: "json", timeout: 30, allow_error: false }),
    outputs: out,
    vars: () => [{ name: "status_code", type: "number" }, { name: "body", type: "string" }, { name: "json", type: "object" }, { name: "headers", type: "object" }],
    summary: (d) => `${d.method || "GET"} ${d.url || ""}`,
  },
  tool: {
    type: "tool", label: "工具", desc: "调用 toolbox 中的工具（文件/联网/命令…）", category: "工具",
    color: "#e879f9", icon: I.tool,
    defaults: () => ({ tool: "", params: {} }),
    outputs: out,
    vars: () => [{ name: "text", type: "string" }, { name: "json", type: "object", desc: "解析后的工具 JSON" }, { name: "summary", type: "string" }, { name: "call_id", type: "string" }, { name: "raw", type: "object" }],
    summary: (d) => d.tool || "未选择工具",
  },
  note: {
    type: "note", label: "注释", desc: "画布便签，不参与执行", category: "基础",
    color: "#fde68a", icon: I.note, noInput: true, noOutput: true,
    defaults: () => ({ text: "在这里写点说明…" }),
    outputs: () => [], vars: () => [],
  },
  iteration_start: {
    type: "iteration_start", label: "起点", desc: "", category: "逻辑", hidden: true,
    color: "#f59e0b", icon: I.start, noInput: true,
    defaults: () => ({}), outputs: out, vars: () => [],
  },
  loop_start: {
    type: "loop_start", label: "起点", desc: "", category: "逻辑", hidden: true,
    color: "#f97316", icon: I.start, noInput: true,
    defaults: () => ({}), outputs: out, vars: () => [],
  },
};

export const CATEGORIES = ["模型", "逻辑", "转换", "工具", "基础"] as const;

export const CONDITION_OPS = [
  { label: "包含", value: "contains" }, { label: "不包含", value: "not_contains" },
  { label: "等于", value: "eq" }, { label: "不等于", value: "ne" },
  { label: "开头是", value: "starts_with" }, { label: "结尾是", value: "ends_with" },
  { label: "为空", value: "empty" }, { label: "不为空", value: "not_empty" },
  { label: "> 大于", value: "gt" }, { label: "< 小于", value: "lt" },
  { label: "≥ 大于等于", value: "ge" }, { label: "≤ 小于等于", value: "le" },
  { label: "正则匹配", value: "regex" }, { label: "长度大于", value: "len_gt" },
];
export const UNARY_OPS = new Set(["empty", "not_empty"]);

export function newNodeId(type: string, existing: Set<string>): string {
  let i = 1;
  while (existing.has(`${type}_${i}`)) i++;
  return `${type}_${i}`;
}
export { uid };
