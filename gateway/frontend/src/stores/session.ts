/** 会话状态：当前会话、事件流、WS 连接与生命周期、流式增量、插话队列。 */
import { defineStore } from "pinia";
import { compactContext, getContextUsage, createSession, deleteSession, getPrompts, listSessions, savePrompts, uploadFile, WS_BASE } from "../api";
import { useSkillsStore } from "./skills";
import type { ContextUsage, GatewayEvent, PromptPreset, SessionInfo, UploadedFile } from "../types";

const ACTIVE_TYPES = ["user_message", "model_call", "assistant_message", "tool_call",
                      "tool_result", "rollback", "interject", "interject_applied",
                      "interject_withdrawn", "agent_resumed", "subagent_notice_applied"];

export interface Interjection { id: string; content: string; state: "queued" | "applied" }

export const useSessionStore = defineStore("session", {
  state: () => ({
    sessions: [] as SessionInfo[],
    currentId: "",
    events: [] as GatewayEvent[],
    connected: false,
    ws: null as WebSocket | null,
    drawer: { callId: "" },
    cancelling: false,
    seenSeq: new Set<number>(),
    prompts: [] as PromptPreset[],
    promptId: "",
    draft: "",
    /** 流式增量：当前轮的实时正文/思考（assistant_message 到达后清空） */
    live: { iteration: 0, content: "", reasoning: "" },
    /** 本地刚发出、还没收到服务端回显的消息（乐观显示） */
    pending: "" as string,
    /** 本会话已上传进工作区的附件（Composer 常驻不随会话重建，open() 时清） */
    attachments: [] as UploadedFile[],
    uploading: 0,           // 在途上传计数（chip 区显示"上传中…"）
    contextUsage: null as ContextUsage | null,
    compacting: false,
    contextNote: "",
    contextRevision: 0,
    contextRequestPending: false,
  }),

  getters: {
    promptOptions(state): { label: string; value: string }[] {
      return [
        { label: "默认（无附加）", value: "" },
        ...state.prompts.map((p) => ({ label: p.name, value: p.id })),
      ];
    },

    running(state): boolean {
      if (state.compacting) return false;
      if (state.cancelling) return false;
      if (state.pending) return true;
      const last = [...state.events].reverse().find(e =>
        ACTIVE_TYPES.includes(e.type) || e.type === 'done' || e.type === 'error');
      if (!last || !ACTIVE_TYPES.includes(last.type)) return false;
      // 打开历史会话回放时，停在工具调用上的陈年事件不能把界面锁死
      return Date.now() - new Date(last.ts).getTime() < 10 * 60_000;
    },
    totalTokens(state): number {
      return state.events
        .filter((e) => e.type === "assistant_message")
        .reduce((s, e) => s + (e.tokens || 0), 0);
    },
    /** tool_call 与 tool_result 配对：优先 tc_id 精确匹配，旧事件退回按工具名就近 */
    toolResults(state): Map<number, GatewayEvent> {
      const out = new Map<number, GatewayEvent>();
      const open: GatewayEvent[] = [];
      for (const e of state.events) {
        if (e.type === "tool_call") open.push(e);
        else if (e.type === "tool_result") {
          let idx = e.tc_id ? open.findIndex((c) => c.tc_id === e.tc_id) : -1;
          if (idx < 0) {
            for (let i = open.length - 1; i >= 0; i--) {
              if (open[i].tool === e.tool) { idx = i; break; }
            }
          }
          if (idx >= 0) out.set(open.splice(idx, 1)[0].seq, e);
        }
      }
      return out;
    },
    /** 当前任务的插话列表（排队中 / 已注入） */
    interjections(state): Interjection[] {
      let start = 0;
      state.events.forEach((e, i) => { if (e.type === "user_message") start = i; });
      const map = new Map<string, Interjection>();
      for (const e of state.events.slice(start)) {
        if (e.type === "interject" && e.id) map.set(e.id, { id: e.id, content: e.content || "", state: "queued" });
        if (e.type === "interject_applied" && e.id && map.has(e.id)) map.get(e.id)!.state = "applied";
        if (e.type === "interject_withdrawn" && e.id) map.delete(e.id);
      }
      return [...map.values()];
    },
    currentTitle(state): string {
      return state.sessions.find((s) => s.session_id === state.currentId)?.title || "新对话";
    },
  },

  actions: {
    async refreshSessions() {
      this.sessions = await listSessions();
    },

    async newSession() {
      const s = await createSession();
      await this.refreshSessions();
      this.open(s.session_id);
    },

    async removeSession(sid: string) {
      await deleteSession(sid);
      await this.refreshSessions();
      if (this.currentId === sid) {
        if (this.sessions.length) this.open(this.sessions[0].session_id);
        else await this.newSession();
      }
    },

    /** 切会话：重连 WS，服务端会先回放历史再推实时 */
    open(sessionId: string, force = false) {
      if (!force && this.ws && this.currentId === sessionId) return;
      this.currentId = sessionId;
      this.events = [];
      this.seenSeq = new Set();
      this.live = { iteration: 0, content: "", reasoning: "" };
      this.pending = "";
      this.attachments = [];
      this.contextUsage = null;
      this.compacting = false;
      this.contextNote = "";
      this.contextRequestPending = false;
      this.contextRevision++;
      this.refreshContext();
      const old = this.ws;
      this.ws = null;
      old?.close();
      const ws = new WebSocket(`${WS_BASE}/ws/sessions/${sessionId}`);
      this.ws = ws;
      ws.onopen = () => { if (this.ws === ws) { this.connected = true; this.refreshContext(); } };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.connected = false;
        setTimeout(() => {
          if (this.currentId === sessionId && this.ws === ws) this.open(sessionId, true);
        }, 2500);
      };
      ws.onmessage = (msg) => { if (this.ws === ws) this.ingest(JSON.parse(msg.data)); };
    },

    ingest(evt: GatewayEvent) {
      if (evt.live) {
        if (evt.type === 'context_usage') {
          this.contextRevision++;
          this.contextUsage = { history_tokens: evt.history_tokens || 0, budget: evt.budget || 64000,
            ratio: evt.ratio || 0, near_limit: !!evt.near_limit };
          if (evt.pending !== undefined && !this.contextRequestPending) {
            this.compacting = evt.pending;
            if (!evt.pending && this.contextNote === '正在停止当前请求并压缩对话…') this.contextNote = '';
          }
        }
        if (evt.type === "assistant_delta") {
          if (evt.reset || evt.iteration !== this.live.iteration) {
            this.live = { iteration: evt.iteration || 0, content: "", reasoning: "" };
          }
          this.live.content += evt.content || "";
          this.live.reasoning += evt.reasoning || "";
        }
        return;
      }
      // 集合去重而不是比大小：顺序异常或 seq 回绕时不至于丢事件
      if (this.seenSeq.has(evt.seq)) return;
      this.seenSeq.add(evt.seq);
      this.events.push(evt);
      if (evt.type === 'context_compaction_started') {
        this.contextRevision++;
        this.compacting = true;
        this.contextNote = '正在停止当前请求并压缩对话…';
      }
      if (evt.type === 'context_usage') {
        this.contextRevision++;
        this.contextUsage = { history_tokens: evt.history_tokens || 0, budget: evt.budget || 64000,
          ratio: evt.ratio || 0, near_limit: !!evt.near_limit };
      }
      if (evt.type === 'context_compacted') {
        this.contextRevision++;
        this.compacting = false;
        this.contextNote = evt.compacted
          ? `已压缩对话：约 ${evt.before_tokens?.toLocaleString()} → ${evt.after_tokens?.toLocaleString()}，下次发送使用压缩后的内容。`
          : evt.reason || '当前内容已足够精简';
      }
      if (evt.type === "user_message") {
        this.pending = "";
        if (!this.sessions.find((s) => s.session_id === this.currentId)?.title) {
          this.refreshSessions();
        }
      }
      if (evt.type === "assistant_message" || evt.type === "model_call") {
        this.live = { iteration: 0, content: "", reasoning: "" };
      }
      if (evt.type === "done" || evt.type === "error") {
        this.cancelling = false;
        this.pending = "";
      }
    },

    /** 空闲时发 = 新任务；运行中发 = 插话（服务端自动判定排队） */
    async refreshContext() {
      const sid = this.currentId, revision = this.contextRevision;
      try {
        const stats = await getContextUsage(sid);
        if (sid === this.currentId && revision === this.contextRevision) {
          this.contextUsage = stats;
          if (!this.contextRequestPending) this.compacting = !!stats.pending;
        }
      } catch { /* Older gateways can still open conversations. */ }
    },

    async compressContext() {
      if (this.compacting || !this.currentId) return;
      const sid = this.currentId;
      this.contextRevision++;
      this.contextRequestPending = true;
      this.compacting = true;
      this.contextNote = "";
      try {
        const response = await compactContext(sid);
        if (sid !== this.currentId) return;
        // The event can arrive before the HTTP response. Do not re-enable a
        // pending state already cleared by context_compacted.
        if (response.pending && this.compacting) this.contextNote = '正在停止当前请求并压缩对话…';
        if (!response.pending) this.compacting = false;
        await this.refreshContext();
      } catch (e: any) {
        if (sid === this.currentId) {
          this.compacting = false;
          this.contextNote = `压缩失败：${e?.message || e}`;
        }
      } finally {
        if (sid === this.currentId) this.contextRequestPending = false;
      }
    },

    send(text: string) {
      const content = text.trim();
      if (!content || this.compacting || !this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
      const wasRunning = this.running;
      this.cancelling = false;
      const skills = useSkillsStore().effective();
      this.ws.send(JSON.stringify({ type: "user_message", content, prompt_id: this.promptId,
                                  toolset_id: localStorage.getItem("agg.toolset") || "",
                                  ...(skills.length ? { skills } : {}) }));
      if (!wasRunning) this.pending = content;
      return true;
    },

    withdraw(id: string) {
      this.ws?.send(JSON.stringify({ type: "withdraw", id }));
    },

    async loadPrompts() {
      this.prompts = await getPrompts();
    },

    async savePromptList(list: PromptPreset[]) {
      await savePrompts(list);
      await this.loadPrompts();
    },

    cancel() {
      if (!this.ws || !this.running) return;
      this.cancelling = true;
      this.ws.send(JSON.stringify({ type: "cancel" }));
    },

    openDrawer(callId: string) {
      this.drawer.callId = callId;
    },

    /** 批量上传（粘贴文件/拖拽/附件按钮共用）。逐个顺序传：并发时服务端
     *  的同名 -2 改名会互相看不到。失败聚合抛给调用方 toast。 */
    async uploadFiles(files: FileList | File[]) {
      const list = Array.from(files);
      if (!list.length || !this.currentId) return;
      const errors: string[] = [];
      for (const f of list) {
        this.uploading++;
        try {
          this.attachments.push(await uploadFile(this.currentId, f));
        } catch (e: any) {
          errors.push(`${f.name}: ${e?.message || e}`);
        } finally {
          this.uploading--;
        }
      }
      if (errors.length) {
        throw new Error(errors.length === list.length
          ? `全部上传失败（${errors[0]}）`
          : `${errors.length}/${list.length} 个上传失败（${errors[0]}）`);
      }
    },

    /** 长文本存档成工作区 .md（粘贴超阈值时省上下文），返回落盘文件名 */
    async uploadText(text: string, filename: string): Promise<UploadedFile> {
      const blob = new Blob([text], { type: "text/markdown" });
      const info = await uploadFile(this.currentId, blob, filename);
      this.attachments.push(info);
      return info;
    },

    /** 仅撤本地 chip：服务端文件与给模型的通知不撤回（读过的证据不能消失） */
    removeAttachment(i: number) {
      this.attachments.splice(i, 1);
    },

    setDraft(text: string) {
      this.draft = text;
    },
  },
});
