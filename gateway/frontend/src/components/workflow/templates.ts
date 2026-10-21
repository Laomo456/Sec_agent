/** 内置工作流模板：新建时一键套用，也是节点用法的活示例。 */
import type { Workflow } from "../../types";

type Tpl = { key: string; name: string; desc: string; icon: string; wf: Omit<Workflow, "id" | "name"> };

const N = (id: string, type: string, x: number, y: number, data: Record<string, any>, extra: Record<string, any> = {}) =>
  ({ id, type, position: { x, y }, data, ...extra });
const E = (s: string, t: string, h = "out") => ({ id: `e_${s}_${h}_${t}`, source: s, target: t, sourceHandle: h });

export const TEMPLATES: Tpl[] = [
  {
    key: "blank", name: "空白工作流", desc: "开始 → LLM → 结束", icon: "✦",
    wf: {
      nodes: [
        N("start", "start", 60, 200, { title: "开始", variables: [{ name: "query", label: "用户输入", type: "paragraph", required: true }] }),
        N("llm_1", "llm", 380, 180, { title: "LLM", system: "你是一个乐于助人的助手。", prompt: "{{start.query}}", temperature: 0.7, output: "text" }),
        N("end", "end", 720, 200, { title: "结束", outputs: [{ name: "answer", value: "{{llm_1.text}}" }] }),
      ],
      edges: [E("start", "llm_1"), E("llm_1", "end")],
    },
  },
  {
    key: "support", name: "智能客服路由", desc: "问题分类 → 分支处理 → 聚合回复", icon: "🎧",
    wf: {
      nodes: [
        N("start", "start", 40, 240, { title: "开始", variables: [{ name: "query", label: "客户问题", type: "paragraph", required: true }] }),
        N("classifier_1", "classifier", 340, 220, { title: "意图识别", input: "{{start.query}}", instruction: "",
          classes: [{ id: "c_pre", name: "售前咨询（价格、功能、购买）" }, { id: "c_after", name: "售后问题（退款、故障、投诉）" }, { id: "c_other", name: "闲聊或其他" }] }),
        N("llm_pre", "llm", 700, 60, { title: "售前顾问", system: "你是热情专业的售前顾问，回答简洁并引导购买。", prompt: "{{start.query}}", temperature: 0.6, output: "text" }),
        N("llm_after", "llm", 700, 240, { title: "售后专员", system: "你是耐心的售后专员。先共情，再给出明确的处理步骤。", prompt: "{{start.query}}", temperature: 0.4, output: "text" }),
        N("template_1", "template", 700, 420, { title: "兜底回复", template: "您好，我是智能助手。关于「{{start.query}}」，建议您联系人工客服获取更准确的帮助。" }),
        N("aggregator_1", "aggregator", 1060, 240, { title: "汇总回复", variables: ["{{llm_pre.text}}", "{{llm_after.text}}", "{{template_1.output}}"] }),
        N("answer_1", "answer", 1360, 240, { title: "回复客户", text: "【{{classifier_1.class_name}}】\n\n{{aggregator_1.output}}" }),
      ],
      edges: [E("start", "classifier_1"), E("classifier_1", "llm_pre", "c_pre"), E("classifier_1", "llm_after", "c_after"),
        E("classifier_1", "template_1", "c_other"), E("llm_pre", "aggregator_1"), E("llm_after", "aggregator_1"),
        E("template_1", "aggregator_1"), E("aggregator_1", "answer_1")],
    },
  },
  {
    key: "batch", name: "批量翻译", desc: "拆分列表 → 并行迭代 LLM → 合并", icon: "🌐",
    wf: {
      nodes: [
        N("start", "start", 40, 180, { title: "开始", variables: [
          { name: "query", label: "待翻译文本（每行一条）", type: "paragraph", required: true },
          { name: "lang", label: "目标语言", type: "select", options: ["英语", "日语", "法语"], required: true }] }),
        N("list_op_1", "list_op", 330, 170, { title: "按行拆分", input: "{{start.query}}", op: "split", sep: "\\n" }),
        N("iteration_1", "iteration", 620, 90, { title: "逐条翻译", input: "{{list_op_1.output}}", output_var: "{{llm_1.text}}", parallel: true, parallel_limit: 4, max_items: 100 },
          { style: { width: "520px", height: "240px" } }),
        N("iteration_1_start", "iteration_start", 30, 100, { title: "起点" }, { parentNode: "iteration_1", extent: "parent" }),
        N("llm_1", "llm", 150, 70, { title: "翻译", system: "你是专业译者，只输出译文。", prompt: "翻译成{{start.lang}}：{{iteration_1.item}}", temperature: 0.2, output: "text" },
          { parentNode: "iteration_1", extent: "parent" }),
        N("list_op_2", "list_op", 1220, 170, { title: "合并结果", input: "{{iteration_1.output}}", op: "join", sep: "\n" }),
        N("end", "end", 1510, 180, { title: "结束", outputs: [{ name: "result", value: "{{list_op_2.output}}" }, { name: "count", value: "{{iteration_1.count}}" }] }),
      ],
      edges: [E("start", "list_op_1"), E("list_op_1", "iteration_1"), E("iteration_1_start", "llm_1"),
        E("iteration_1", "list_op_2"), E("list_op_2", "end")],
    },
  },
  {
    key: "research", name: "联网调研报告", desc: "提取关键词 → 智能体搜索 → 条件质检 → 成文", icon: "🔎",
    wf: {
      nodes: [
        N("start", "start", 40, 220, { title: "开始", variables: [{ name: "query", label: "调研主题", type: "text", required: true }] }),
        N("extractor_1", "extractor", 330, 200, { title: "提取关键词", input: "{{start.query}}", instruction: "",
          params: [{ name: "keywords", type: "string", description: "适合搜索引擎的关键词，空格分隔", required: true },
            { name: "angle", type: "string", description: "调研侧重点", required: false }] }),
        N("agent_1", "agent", 640, 200, { title: "搜索资料", task: "用联网工具搜索「{{extractor_1.keywords}}」，侧重{{extractor_1.angle}}。整理 5 条以内关键事实，每条注明来源链接。", system: "", max_iterations: 8, on_error: "continue" }),
        N("if_else_1", "if_else", 950, 200, { title: "资料是否充足", cases: [{ id: "case_ok", logic: "and", conditions: [
          { var: "{{agent_1.text}}", op: "not_empty", value: "" }, { var: "{{agent_1.text}}", op: "contains", value: "http" }] }] }),
        N("llm_1", "llm", 1260, 100, { title: "撰写报告", system: "你是严谨的行业分析师。", prompt: "基于以下资料，为「{{start.query}}」写一份结构化调研简报（背景/要点/结论），引用来源：\n\n{{agent_1.text}}", temperature: 0.5, output: "text" }),
        N("llm_2", "llm", 1260, 320, { title: "基于常识作答", system: "你是行业分析师。资料不足时明确说明信息局限。", prompt: "未能检索到足够资料。请基于已有知识简述「{{start.query}}」，并注明需要核实的点。", temperature: 0.5, output: "text" }),
        N("answer_1", "answer", 1580, 100, { title: "输出报告", text: "{{llm_1.text}}" }),
        N("answer_2", "answer", 1580, 320, { title: "输出简述", text: "⚠ 资料不足，以下为初步判断：\n\n{{llm_2.text}}" }),
      ],
      edges: [E("start", "extractor_1"), E("extractor_1", "agent_1"), E("agent_1", "if_else_1"),
        E("if_else_1", "llm_1", "case_ok"), E("if_else_1", "llm_2", "else"), E("llm_1", "answer_1"), E("llm_2", "answer_2")],
    },
  },
  {
    key: "api", name: "API 数据加工", desc: "HTTP 请求 → 代码清洗 → LLM 解读", icon: "⚡",
    wf: {
      nodes: [
        N("start", "start", 40, 200, { title: "开始", variables: [{ name: "query", label: "GitHub 仓库（owner/repo）", type: "text", required: true }] }),
        N("http_1", "http", 330, 180, { title: "获取仓库信息", method: "GET", url: "https://api.github.com/repos/{{start.query}}", headers: [{ key: "Accept", value: "application/vnd.github+json" }], params: [], body: "", body_type: "json", timeout: 20, on_error: "branch" }),
        N("code_1", "code", 640, 120, { title: "提取字段", timeout: 30,
          inputs: [{ name: "data", value: "{{http_1.json}}" }],
          code: "def main(data):\n    data = data or {}\n    return {\n        \"stars\": data.get(\"stargazers_count\", 0),\n        \"lang\": data.get(\"language\") or \"未知\",\n        \"desc\": data.get(\"description\") or \"\",\n    }\n",
          outputs: [{ name: "stars", type: "number" }, { name: "lang", type: "string" }, { name: "desc", type: "string" }] }),
        N("llm_1", "llm", 950, 120, { title: "解读", system: "", prompt: "仓库 {{start.query}}：{{code_1.desc}}，主要语言 {{code_1.lang}}，{{code_1.stars}} 星。用三句话点评它的定位和热度。", temperature: 0.6, output: "text" }),
        N("answer_1", "answer", 1260, 120, { title: "回复", text: "{{llm_1.text}}" }),
        N("answer_2", "answer", 950, 330, { title: "请求失败", text: "获取仓库信息失败：{{http_1.error}}" }),
      ],
      edges: [E("start", "http_1"), E("http_1", "code_1"), E("http_1", "answer_2", "fail"), E("code_1", "llm_1"), E("llm_1", "answer_1")],
    },
  },
];
