/** 工具展示用的元信息：危险等级配色、来源/种类标签、schema → 参数表。 */
import type { ToolCard } from "../../types";

export const DANGER: Record<string, { label: string; color: string; icon: string; desc: string }> = {
  readonly: { label: "只读", color: "#34d399", icon: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", desc: "只读取信息，不改变任何东西" },
  intrusive: { label: "有副作用", color: "#fbbf24", icon: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", desc: "会写文件、发请求或执行命令" },
  destructive: { label: "破坏性", color: "#f87171", icon: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12", desc: "可能删除数据或造成不可逆影响" },
  unknown: { label: "未标注", color: "#94a3b8", icon: "M12 17h.01M9.1 9a3 3 0 1 1 3.9 2.8c-.6.3-1 .9-1 1.5V14", desc: "来源未声明副作用等级" },
};
export const dangerMeta = (d?: string) => DANGER[d || "unknown"] || DANGER.unknown;

export const kindLabel: Record<string, string> = { script: "脚本", http: "HTTP", command: "命令" };

export function sourceLabel(t: ToolCard): string {
  if (t.source === "skill") return "Skill";
  if (t.source?.startsWith("mcp:")) return `MCP · ${t.source.slice(4)}`;
  return t.origin === "user" ? "自建" : "内置";
}

export interface ParamRow { name: string; type: string; required: boolean; desc: string; enum?: string[]; default?: any }

export function paramRows(schema: any): ParamRow[] {
  const props = schema?.properties || {};
  const req = new Set<string>(schema?.required || []);
  return Object.entries<any>(props).map(([name, s]) => ({
    name,
    type: s.type === "array" ? `${s.items?.type || "any"}[]` : s.type || "any",
    required: req.has(name),
    desc: s.description || "",
    enum: s.enum,
    default: s.default,
  }));
}

/** 按 schema 生成一份占位参数（试运行表单的初值） */
export function sampleParams(t: ToolCard): Record<string, any> {
  if (t.example && Object.keys(t.example).length) return JSON.parse(JSON.stringify(t.example));
  const out: Record<string, any> = {};
  for (const r of paramRows(t.params_schema)) {
    if (!r.required) continue;
    out[r.name] = r.enum ? r.enum[0] : r.type === "number" || r.type === "integer" ? 0
      : r.type === "boolean" ? false : r.type.endsWith("[]") ? [] : "";
  }
  return out;
}
