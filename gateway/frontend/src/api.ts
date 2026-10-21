/** REST 接口封装。gateway 托管时同源（页面从哪个端口打开就连哪个口，
 *  改 server.port 不用重新打包）；开发模式(vite:5173)才需要写死网关地址。 */
import type {
  KnowledgeDoc, KnowledgeHit, KnowledgeStatus,
  OutputPage, PromptPreset, SessionInfo, SkillDetail, SkillInfo, ToolCard, ToolPreset, ToolsState, UploadedFile,
  Workflow, WorkflowSummary,
  SubagentJob,
  ContextUsage,
} from "./types";

export const BASE: string =
  (import.meta as any).env?.VITE_API_BASE
  ?? ((import.meta as any).DEV ? "http://127.0.0.1:8300" : window.location.origin);
export const WS_BASE: string = BASE.replace(/^http/, "ws");

export async function listSessions(): Promise<SessionInfo[]> {
  const r = await fetch(`${BASE}/api/sessions`);
  return r.json();
}

export async function createSession(): Promise<SessionInfo> {
  const r = await fetch(`${BASE}/api/sessions`, { method: "POST" });
  return r.json();
}

export async function deleteSession(sid: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${sid}`, { method: "DELETE" });
}

export async function getPrompts(): Promise<PromptPreset[]> {
  const r = await fetch(`${BASE}/api/prompts`);
  return r.json();
}

export async function savePrompts(list: PromptPreset[]): Promise<void> {
  // 失败必须抛出：以前不看状态码，422 也当成功，弹窗关掉但什么都没存
  const res = await fetch(`${BASE}/api/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(list),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = JSON.stringify((await res.json()).detail ?? detail); } catch { /* 非 JSON 错误体 */ }
    throw new Error(`保存失败：${detail}`);
  }
}

export async function fetchOutput(
  sessionId: string,
  callId: string,
  opts: { offset?: number; limit?: number; grep?: string },
): Promise<OutputPage> {
  const q = new URLSearchParams();
  if (opts.offset) q.set("offset", String(opts.offset));
  if (opts.limit) q.set("limit", String(opts.limit));
  if (opts.grep) q.set("grep", opts.grep);
  const r = await fetch(`${BASE}/api/outputs/${sessionId}/${callId}?${q}`);
  return r.json();
}

/** 上传文件进会话工作区（粘贴/拖拽/附件按钮共用）。
 *  不能设 Content-Type：multipart boundary 必须由浏览器生成。 */
export async function uploadFile(sessionId: string, file: Blob, filename?: string): Promise<UploadedFile> {
  const fd = new FormData();
  fd.append("file", file, filename || (file instanceof File ? file.name : "upload.bin"));
  return json(fetch(`${BASE}/api/sessions/${sessionId}/files`, { method: "POST", body: fd }));
}

async function json<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail);
  }
  return res.json();
}
const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const getContextUsage = (sid: string): Promise<ContextUsage> =>
  json(fetch(`${BASE}/api/sessions/${sid}/context`));
export const compactContext = (sid: string): Promise<Partial<ContextUsage>> =>
  json(fetch(`${BASE}/api/sessions/${sid}/context/compact`, jsonInit('POST')));

export const getSubagents = (sid: string): Promise<{ jobs: SubagentJob[] }> =>
  json(fetch(`${BASE}/api/sessions/${sid}/subagents`));
export const cancelSubagentBatch = (sid: string, batch: string): Promise<{ ok: boolean }> =>
  json(fetch(`${BASE}/api/sessions/${sid}/subagents/${batch}/cancel`, jsonInit("POST")));
export const stopSubagent = (sid: string, id: string): Promise<{ ok: boolean }> =>
  json(fetch(`${BASE}/api/sessions/${sid}/subagents/${id}/stop`, jsonInit("POST")));
export const subagentReportUrl = (sid: string, id: string): string =>
  `${BASE}/api/sessions/${sid}/subagents/${id}/report`;

export const getSettings = (): Promise<any> => json(fetch(`${BASE}/api/settings`));
export const saveSettings = (data: any): Promise<any> =>
  json(fetch(`${BASE}/api/settings`, jsonInit("PUT", data)));
/** 轻量模型档案列表（无密钥），工作流节点下拉用 */
export const getModelProfiles = (): Promise<any[]> =>
  json(fetch(`${BASE}/api/model-profiles`));

/* ═══ 工作流 ═══ */
export const listWorkflows = (): Promise<WorkflowSummary[]> =>
  json(fetch(`${BASE}/api/workflows`));
export const createWorkflow = (data: Partial<Workflow> = {}): Promise<Workflow> =>
  json(fetch(`${BASE}/api/workflows`, jsonInit("POST", data)));
export const getWorkflow = (id: string): Promise<Workflow> =>
  json(fetch(`${BASE}/api/workflows/${id}`));
export const saveWorkflow = (wf: Workflow): Promise<Workflow> =>
  json(fetch(`${BASE}/api/workflows/${wf.id}`, jsonInit("PUT", wf)));
export const deleteWorkflow = (id: string): Promise<void> =>
  json(fetch(`${BASE}/api/workflows/${id}`, { method: "DELETE" }));
export const listRuns = (id: string): Promise<any[]> =>
  json(fetch(`${BASE}/api/workflows/${id}/runs`));
export const getRun = (runId: string): Promise<any> =>
  json(fetch(`${BASE}/api/workflow-runs/${runId}`));
export async function uploadWorkflowFile(id: string, file: File): Promise<UploadedFile> {
  const body = new FormData();
  body.append("file", file);
  return json(fetch(`${BASE}/api/workflows/${id}/files`, { method: "POST", body }));
}
export const getToolCards = (): Promise<ToolCard[]> => json(fetch(`${BASE}/api/tool-cards`));

/* ═══ 技能库（Skill 栏） ═══ */
export const listSkills = (): Promise<SkillInfo[]> => json(fetch(`${BASE}/api/skills`));
export const getSkill = (name: string): Promise<SkillDetail> =>
  json(fetch(`${BASE}/api/skills/${encodeURIComponent(name)}`));
export const saveSkill = (body: { name: string; description?: string; content: string }): Promise<SkillDetail> =>
  json(fetch(`${BASE}/api/skills`, jsonInit("POST", body)));
export const deleteSkill = (name: string): Promise<{ ok: boolean }> =>
  json(fetch(`${BASE}/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }));

/* ═══ 工具配置 / 工具工坊 ═══ */
export const getTools = (): Promise<ToolsState> => json(fetch(`${BASE}/api/tools`));
export const saveToolsets = (data: { disabled: string[]; default: string; presets: ToolPreset[] }): Promise<any> =>
  json(fetch(`${BASE}/api/toolsets`, jsonInit("PUT", data)));
export const getToolSource = (name: string): Promise<any> => json(fetch(`${BASE}/api/tools/${name}/source`));
export const createTool = (spec: any): Promise<any> => json(fetch(`${BASE}/api/tools`, jsonInit("POST", spec)));
export const updateTool = (name: string, spec: any): Promise<any> =>
  json(fetch(`${BASE}/api/tools/${name}`, jsonInit("PUT", spec)));
export const deleteTool = (name: string): Promise<void> =>
  json(fetch(`${BASE}/api/tools/${name}`, { method: "DELETE" }));
export const tryTool = (name: string, params: any): Promise<any> =>
  json(fetch(`${BASE}/api/tools/${name}/try`, jsonInit("POST", { params })));
export const searchToolsPreview = (query: string, toolset: string): Promise<{ results: ToolCard[]; hint?: string }> =>
  json(fetch(`${BASE}/api/tools/search`, jsonInit("POST", { query, toolset })));

/* ═══ 知识库(RAG) ═══ */
export const kbStatus = (): Promise<KnowledgeStatus> => json(fetch(`${BASE}/api/knowledge/status`));
export const kbGetConfig = (): Promise<any> => json(fetch(`${BASE}/api/knowledge/config`));
export const kbSaveConfig = (cfg: any): Promise<any> =>
  json(fetch(`${BASE}/api/knowledge/config`, jsonInit("PUT", cfg)));
export const kbListDocs = (collection = "", q = "", limit = 50, offset = 0):
  Promise<{ total: number; documents: KnowledgeDoc[] }> => {
  const p = new URLSearchParams();
  if (collection) p.set("collection", collection);
  if (q) p.set("q", q);
  p.set("limit", String(limit));
  p.set("offset", String(offset));
  return json(fetch(`${BASE}/api/knowledge/documents?${p}`));
};
export const kbGetDoc = (id: number): Promise<any> =>
  json(fetch(`${BASE}/api/knowledge/documents/${id}`));
export const kbDeleteDoc = (id: number): Promise<any> =>
  json(fetch(`${BASE}/api/knowledge/documents/${id}`, { method: "DELETE" }));
export const kbSearch = (body: { query: string; collection?: string; mode?: string; top_k?: number }):
  Promise<{ results: KnowledgeHit[]; count: number; mode: string; warning?: string }> =>
  json(fetch(`${BASE}/api/knowledge/search`, jsonInit("POST", body)));
export const kbIngestPath = (body: { path: string; collection?: string; title?: string; overwrite?: boolean }):
  Promise<any> => json(fetch(`${BASE}/api/knowledge/ingest-path`, jsonInit("POST", body)));
/** 上传文档入库。不能设 Content-Type:multipart boundary 必须由浏览器生成。 */
export async function kbUpload(file: File, collection: string, title = ""): Promise<any> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("collection", collection || "general");
  if (title) fd.append("title", title);
  return json(fetch(`${BASE}/api/knowledge/upload`, { method: "POST", body: fd }));
}
