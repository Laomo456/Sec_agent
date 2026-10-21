/** Gateway⇄前端事件契约（与 gateway/events.py 对应） */
export interface GatewayEvent {
  seq: number;
  ts: string;
  session: string;
  live?: boolean;          // 只推不落盘的实时事件（assistant_delta）
  type:
    | "user_message"
    | "skill_injected"
    | "model_call"
    | "assistant_message"
    | "assistant_delta"
    | "tool_call"
    | "tool_result"
    | "rollback"
    | "interject"
    | "interject_applied"
    | "interject_withdrawn"
    | "file_upload"
    | "subagent_state"
    | "agent_resumed"
    | "subagent_notice_applied"
    | "context_usage"
    | "context_compacted"
    | "context_compaction_started"
    | "done"
    | "error";
  id?: string;             // interject*：插话 id
  name?: string;           // file_upload：文件名
  size?: number;           // file_upload：字节数
  content?: string;        // user_message / assistant_message / done / interject
  reasoning?: string;      // assistant_message 思考链
  iteration?: number;      // 第几轮循环
  tokens?: number;         // assistant_message 模型 token
  surface?: "meta" | "native";
  prompt?: string;         // user_message：命中的提示词预设名
  tool?: string;           // tool_call / tool_result
  tc_id?: string;          // tool_call ↔ tool_result 精确配对
  params?: Record<string, unknown>;
  call_id?: string;        // tool_result → 钻取全量输出
  result?: string;         // tool_result 预览
  to_iteration?: number;
  note?: string;           // rollback：墓碑摘要
  detail?: string;         // error
  reset?: boolean;         // assistant_delta：清空本轮已显示增量
  agent_id?: string;
  status?: SubagentStatus;
  text?: string;
  iterations?: number;
  tool_calls?: number;
  history_tokens?: number;
  budget?: number;
  ratio?: number;
  near_limit?: boolean;
  pending?: boolean;
  compacted?: boolean;
  reason?: string;
  before_tokens?: number;
  after_tokens?: number;
}

export interface ContextUsage {
  history_tokens: number;
  budget: number;
  ratio: number;
  near_limit: boolean;
  pending?: boolean;
}

export type SubagentStatus = "queued" | "running" | "writing" | "ok" | "error" | "incomplete" | "timeout" | "cancelled" | "interrupted";
export interface SubagentJob {
  agent_id: string;
  batch_id: string;
  session_id: string;
  name: string;
  task: string;
  status: SubagentStatus;
  phase: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number;
  iterations: number;
  tool_calls: number;
  detail: string;
  manifest_path: string;
  report_path: string;
  notification_ack: boolean;
}

export interface SessionInfo {
  session_id: string;
  title?: string;
  updated?: number;
}

export interface PromptPreset {
  id: string;
  name: string;
  content: string;
}

/* ═══ 技能库（Skill 栏） ═══ */
export interface SkillInfo {
  name: string;
  description: string;
  chars: number;
  files: number;                    // 参考文件数（不含 SKILL.md）
}
export interface SkillDetail {
  name: string;
  description: string;
  content: string;
  files: { path: string; name: string; size: number }[];   // 全部文件含 SKILL.md
}

export interface OutputPage {
  status: string;
  total_lines?: number;
  offset?: number;
  returned?: number;
  lines?: string[];
  next_offset?: number;
}

/** 上传结果（POST /api/sessions/{sid}/files 的响应） */
export interface UploadedFile {
  name: string;
  size: number;
  path: string;
}

/* ═══ 工作流 ═══ */
export interface WfNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, any>;
  parentNode?: string;
  extent?: "parent";
  style?: Record<string, any>;
  width?: number;
  height?: number;
}

export interface WfEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  nodes: WfNode[];
  edges: WfEdge[];
  updated?: string;
  problems?: string[];
  cleanup_tools?: { tool: string; params: Record<string, any> }[];
}

export interface WorkflowSummary {
  id: string;
  name: string;
  description: string;
  nodes: number;
  updated: string;
}

export type NodeRunStatus = "running" | "succeeded" | "failed" | "skipped" | "exception" | "stopped" | "stopping" | "skipping" | "manual" | "cached";

export interface WfEvent {
  command?: string;
  node_key?: string;
  reasoning?: string;
  reset?: boolean;
  seq: number;
  ts: string;
  type: string;
  live?: boolean;
  run_id?: string;
  node_id?: string;
  label?: string;
  status?: string;
  title?: string;
  duration_ms?: number;
  outputs?: Record<string, any>;
  answers?: string[];
  error?: string | null;
  detail?: string;
  text?: string;
  msg?: string;
  level?: string;
  inputs?: Record<string, any>;
}

export interface ToolCard {
  name: string;
  sig?: string;             // 一行调用签名 file_read(path: str, offset?: int)
  description: string;
  category?: string;
  danger?: string;
  params_schema?: any;
  example?: any;
  usage?: string;
  source?: string;          // builtin / skill / mcp:<源>
  // 管理页字段（dump_tools admin=true）
  keywords?: string[];
  timeout?: number;
  requires?: string[];
  kind?: "script" | "http" | "command";
  origin?: "builtin" | "user";
  editable?: boolean;
  enabled?: boolean;
}

export interface ToolPreset {
  id: string;
  name: string;
  description?: string;
  tools: string[];          // ["*"] = 不限
  system?: string;
  builtin?: boolean;
}

export interface ToolsState {
  tools: ToolCard[];
  disabled: string[];
  default: string;
  presets: ToolPreset[];
}

/* ═══ 知识库(RAG) ═══ */
export interface KnowledgeCollection {
  collection: string;
  documents: number;
  chunks: number;
}

export interface KnowledgeDoc {
  id: number;
  collection: string;
  title: string;
  source: string;
  path: string;
  size: number;
  chunks: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeHit {
  document_id: number;
  chunk_id: number;
  chunk_index: number;
  title: string;
  collection: string;
  source: string;
  path: string;
  content: string;
  score: number;
  similarity?: number;
  truncated?: boolean;
}

export interface KnowledgeStatus {
  status: string;
  database: string;
  fts5: boolean;
  fts_tokenizer: string;
  documents: number;
  chunks: number;
  collections: KnowledgeCollection[];
  config: Record<string, any>;
}
