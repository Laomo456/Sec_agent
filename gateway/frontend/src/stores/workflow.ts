/** 工作流编辑器状态：列表、当前画布（nodes/edges）、选中、撤销栈、运行态。 */
import { defineStore } from "pinia";
import {
  createWorkflow, deleteWorkflow, getToolCards, getWorkflow, listRuns, listWorkflows, saveWorkflow, WS_BASE,
} from "../api";
import type { ToolCard, WfEvent, WfNode, Workflow, WorkflowSummary } from "../types";
import { NODE_DEFS, handlesOf, newNodeId, type VarDef } from "../components/workflow/nodeDefs";

export interface NodeRun {
  status: string;
  duration_ms?: number;
  outputs?: Record<string, any>;
  error?: string | null;
  label?: string;
  stream?: string;
  reasoning?: string;
  iterations?: number;       // 容器节点内部被执行的次数
  touched?: number;          // 最后一次有动静的时间戳(ms)：流式增量/状态变化/日志
}

export interface VarGroup { nodeId: string; title: string; color: string; icon: string; vars: VarDef[] }

const snapshot = (wf: Workflow | null) => JSON.stringify(wf ? { n: wf.nodes, e: wf.edges, name: wf.name, d: wf.description } : null);

export const useWorkflowStore = defineStore("workflow", {
  state: () => ({
    list: [] as WorkflowSummary[],
    wf: null as Workflow | null,
    selectedId: "" as string,
    savedSnap: "",
    undoStack: [] as string[],
    redoStack: [] as string[],
    saving: false,
    problems: [] as string[],
    // 运行
    ws: null as WebSocket | null,
    runId: "",
    running: false,
    stopping: false,
    pendingCommand: "",
    commandNodeKey: "",
    debugNodeId: "",
    wsConnecting: null as Promise<WebSocket> | null,
    runStatus: "" as "" | "running" | "succeeded" | "failed" | "stopped",
    runError: "",
    runOutputs: {} as Record<string, any>,
    runAnswers: [] as string[],
    runDuration: 0,
    nodeRuns: {} as Record<string, NodeRun>,
    logs: [] as WfEvent[],
    history: [] as any[],
    tools: [] as ToolCard[],
    toolsLoaded: false,
    panel: "" as "" | "run" | "history",
    placing: "" as string,   // 触摸端"点选→点画布放置"模式中待放置的节点类型，空串表示关闭
  }),

  getters: {
    dirty(state): boolean { return !!state.wf && snapshot(state.wf) !== state.savedSnap; },
    selected(state): WfNode | null { return state.wf?.nodes.find((n) => n.id === state.selectedId) || null; },
    startNode(state): WfNode | null {
      return state.wf?.nodes.find((n) => n.type === "start" && !n.parentNode) || null;
    },
  },

  actions: {
    async refresh() { this.list = await listWorkflows(); },

    async open(id: string) {
      this.closeRun();
      this.wf = await getWorkflow(id);
      this.savedSnap = snapshot(this.wf);
      this.undoStack = [];
      this.redoStack = [];
      this.selectedId = "";
      this.problems = [];
      this.resetRun();
      this.panel = "";
      this.loadHistory();
      localStorage.setItem("agg.wf", id);
    },

    async create(name = "未命名工作流", template?: Partial<Workflow>) {
      const wf = await createWorkflow(template ? { ...template, name } : { name });
      await this.refresh();
      await this.open(wf.id);
    },

    async duplicate(id: string) {
      const src = await getWorkflow(id);
      await this.create(`${src.name} 副本`, src);
    },

    async remove(id: string) {
      await deleteWorkflow(id);
      await this.refresh();
      if (this.wf?.id === id) {
        this.wf = null;
        if (this.list.length) await this.open(this.list[0].id);
      }
    },

    async save() {
      if (!this.wf) return;
      this.saving = true;
      try {
        const saved = await saveWorkflow(this.wf);
        this.problems = saved.problems || [];
        this.wf.updated = saved.updated;
        this.savedSnap = snapshot(this.wf);
        await this.refresh();
      } finally {
        this.saving = false;
      }
    },

    /* ── 撤销/重做：每次结构性修改前 checkpoint() ── */
    checkpoint() {
      if (!this.wf) return;
      const s = JSON.stringify({ nodes: this.wf.nodes, edges: this.wf.edges });
      if (this.undoStack[this.undoStack.length - 1] === s) return;
      this.undoStack.push(s);
      if (this.undoStack.length > 80) this.undoStack.shift();
      this.redoStack = [];
    },
    undo() {
      if (!this.wf || !this.undoStack.length) return;
      this.redoStack.push(JSON.stringify({ nodes: this.wf.nodes, edges: this.wf.edges }));
      const s = JSON.parse(this.undoStack.pop()!);
      this.wf.nodes = s.nodes;
      this.wf.edges = s.edges;
    },
    redo() {
      if (!this.wf || !this.redoStack.length) return;
      this.undoStack.push(JSON.stringify({ nodes: this.wf.nodes, edges: this.wf.edges }));
      const s = JSON.parse(this.redoStack.pop()!);
      this.wf.nodes = s.nodes;
      this.wf.edges = s.edges;
    },

    /* ── 节点/连线编辑 ── */
    addNode(type: string, position: { x: number; y: number }, parentNode?: string): WfNode | null {
      if (!this.wf) return null;
      const def = NODE_DEFS[type];
      if (!def) return null;
      this.checkpoint();
      const ids = new Set<string>(this.wf.nodes.map((n: WfNode) => n.id));
      const node: WfNode = {
        id: newNodeId(type, ids), type, position,
        data: { title: def.label, ...def.defaults() },
      };
      if (parentNode) { node.parentNode = parentNode; node.extent = "parent"; }
      if (def.container) {
        node.style = { width: "560px", height: "300px" };
        this.wf.nodes.push(node);
        const st = type === "iteration" ? "iteration_start" : "loop_start";
        this.wf.nodes.push({
          id: `${node.id}_start`, type: st, position: { x: 30, y: 110 },
          parentNode: node.id, extent: "parent", data: { title: "起点" },
        });
      } else {
        this.wf.nodes.push(node);
      }
      this.selectedId = node.id;
      return node;
    },

    removeNodes(ids: string[]) {
      if (!this.wf) return;
      const kill = new Set(ids.filter((id) => {
        const n = this.wf!.nodes.find((x) => x.id === id);
        // 开始节点与容器起点不可单独删
        return n && !(n.type === "start" && !n.parentNode) && !n.type.endsWith("_start");
      }));
      if (!kill.size) return;
      this.checkpoint();
      // 删容器连同子节点
      let grew = true;
      while (grew) {
        grew = false;
        for (const n of this.wf.nodes) {
          if (n.parentNode && kill.has(n.parentNode) && !kill.has(n.id)) { kill.add(n.id); grew = true; }
        }
      }
      this.wf.nodes = this.wf.nodes.filter((n) => !kill.has(n.id));
      this.wf.edges = this.wf.edges.filter((e) => !kill.has(e.source) && !kill.has(e.target));
      if (kill.has(this.selectedId)) this.selectedId = "";
    },

    duplicateNode(id: string) {
      if (!this.wf) return;
      const src = this.wf.nodes.find((n) => n.id === id);
      if (!src || src.type === "start" || src.type.endsWith("_start") || NODE_DEFS[src.type]?.container) return;
      this.checkpoint();
      const ids = new Set<string>(this.wf.nodes.map((n: WfNode) => n.id));
      const copy: WfNode = JSON.parse(JSON.stringify(src));
      copy.id = newNodeId(src.type, ids);
      copy.position = { x: src.position.x + 40, y: src.position.y + 60 };
      copy.data.title = `${src.data.title} 副本`;
      this.wf.nodes.push(copy);
      this.selectedId = copy.id;
    },

    addEdge(e: { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }) {
      if (!this.wf || e.source === e.target) return;
      const exists = this.wf.edges.some((x) => x.source === e.source && x.target === e.target
        && (x.sourceHandle || "out") === (e.sourceHandle || "out"));
      if (exists) return;
      this.checkpoint();
      this.wf.edges.push({
        id: `e_${e.source}_${e.sourceHandle || "out"}_${e.target}`,
        source: e.source, target: e.target, sourceHandle: e.sourceHandle || "out", targetHandle: e.targetHandle || null,
      });
    },

    removeEdges(ids: string[]) {
      if (!this.wf) return;
      this.checkpoint();
      const s = new Set(ids);
      this.wf.edges = this.wf.edges.filter((e) => !s.has(e.id));
    },

    /** 节点出口变化后（删分类/删分支）清理悬空连线 */
    pruneEdges(nodeId: string) {
      if (!this.wf) return;
      const node = this.wf.nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const valid = new Set(handlesOf(node).map((h) => h.id));
      this.wf.edges = this.wf.edges.filter((e) => e.source !== nodeId || valid.has(e.sourceHandle || "out"));
    },

    /* ── 变量选择器：某节点可引用的上游变量（沿入边回溯 + 所在容器） ── */
    upstreamVars(nodeId: string): VarGroup[] {
      if (!this.wf) return [];
      const nodes = new Map<string, WfNode>(this.wf.nodes.map((n: WfNode) => [n.id, n]));
      const incoming = new Map<string, string[]>();
      for (const e of this.wf.edges) {
        if (!incoming.has(e.target)) incoming.set(e.target, []);
        incoming.get(e.target)!.push(e.source);
      }
      const seen = new Set<string>();
      const visit = (id: string) => {
        for (const src of incoming.get(id) || []) {
          if (seen.has(src)) continue;
          seen.add(src);
          visit(src);
        }
        // 容器内节点：容器的上游、容器本身（item/index）也可见
        const p = nodes.get(id)?.parentNode;
        if (p && !seen.has(p)) { seen.add(p); visit(p); }
      };
      visit(nodeId);
      const groups: VarGroup[] = [{
        nodeId: "sys", title: "系统变量", color: "#94a3b8", icon: "M4 4h16v16H4z",
        vars: [{ name: "query", type: "string", desc: "开始节点的 query" }, { name: "run_at", type: "string" }],
      }];
      const envNames = new Set<string>();
      for (const n of this.wf.nodes) {
        if (n.type === "assigner") (n.data.items || []).forEach((i: any) => i.name && envNames.add(i.name));
      }
      if (envNames.size) {
        groups.push({ nodeId: "env", title: "会话变量", color: "#c084fc", icon: "M4 4h16v16H4z",
          vars: [...envNames].map((name) => ({ name, type: "any" })) });
      }
      for (const id of seen) {
        const n = nodes.get(id);
        const def = n && NODE_DEFS[n.type];
        if (!n || !def || def.hidden) continue;
        let vars = def.vars(n.data);
        // 容器的 item/index 只对内部节点可见；外部只看 output
        const inside = nodes.get(nodeId)?.parentNode === id;
        if (def.container) vars = vars.filter((v) => inside ? true : !v.desc?.includes("容器内"));
        if (vars.length) groups.push({ nodeId: id, title: n.data.title || def.label, color: def.color, icon: def.icon, vars });
      }
      return groups;
    },

    /* ── 运行 ── */
    resetRun() {
      this.stopping = false;
      this.pendingCommand = "";
      this.commandNodeKey = "";
      this.debugNodeId = "";
      this.runId = "";
      this.runStatus = "";
      this.runError = "";
      this.runOutputs = {};
      this.runAnswers = [];
      this.runDuration = 0;
      this.nodeRuns = {};
      this.logs = [];
    },

    ensureWs(): Promise<WebSocket> {
      if (!this.wf) return Promise.reject(new Error("no workflow"));
      if (this.ws && this.ws.readyState === WebSocket.OPEN) return Promise.resolve(this.ws);
      if (this.ws && this.ws.readyState === WebSocket.CONNECTING && this.wsConnecting) return this.wsConnecting;
      const ws = new WebSocket(`${WS_BASE}/ws/workflows/${this.wf.id}/run`);
      this.ws = ws;
      ws.onmessage = (m) => { if (this.ws === ws) this.ingest(JSON.parse(m.data)); };
      ws.onclose = () => {
        if (this.ws === ws) {
          this.ws = null;
          this.wsConnecting = null;
          this.finishNodes("stopped");
          if (this.running) this.runStatus = "stopped";
          this.running = false;
          this.stopping = false;
          this.pendingCommand = "";
          this.commandNodeKey = "";
        }
      };
      this.wsConnecting = new Promise((resolve, reject) => {
        ws.onopen = () => { this.wsConnecting = null; resolve(ws); };
        ws.onerror = () => reject(new Error("无法连接工作流运行通道"));
        ws.addEventListener("close", () => reject(new Error("工作流运行通道已断开")), { once: true });
      });
      return this.wsConnecting;
    },

    closeRun() {
      const ws = this.ws;
      this.ws = null;
      ws?.close();
      this.running = false;
      this.stopping = false;
      this.pendingCommand = "";
      this.commandNodeKey = "";
      this.wsConnecting = null;
      this.finishNodes("stopped");
    },

    finishNodes(status: string) {
      for (const r of Object.values(this.nodeRuns) as NodeRun[]) {
        if (["running", "skipping", "stopping"].includes(r.status)) r.status = status;
      }
    },

    async sendRunCommand(message: Record<string, any>, terminal = true) {
      try {
        const ws = await this.ensureWs();
        if (this.stopping) throw new Error("工作流正在停止");
        ws.send(JSON.stringify(message));
      } catch (error: any) {
        this.pendingCommand = "";
        this.commandNodeKey = "";
        this.runError = error?.message || "工作流请求失败";
        if (terminal) {
          this.running = false;
          this.stopping = false;
          this.runStatus = "failed";
          this.finishNodes("stopped");
        }
        return false;
      }
      return true;
    },

    async run(inputs: Record<string, any>) {
      if (!this.wf || this.running || this.pendingCommand) return;
      this.resetRun();
      this.running = true;
      this.runStatus = "running";
      this.panel = "run";
      this.pendingCommand = "run";
      // 发未保存的草稿：所见即所跑
      await this.sendRunCommand({ type: "run", inputs, workflow: this.wf });
    },

    async runNode(nodeId: string, inputs: Record<string, any>) {
      if (!this.wf || this.running || this.pendingCommand) return;
      this.resetRun();
      this.running = true;
      this.runStatus = "running";
      this.debugNodeId = nodeId;
      this.pendingCommand = "debug";
      this.nodeRuns[nodeId] = { status: "running" };
      await this.sendRunCommand({ type: "run_node", node_id: nodeId, inputs, workflow: this.wf });
    },

    stop() {
      if (!this.running || this.stopping || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.stopping = true;
      this.finishNodes("stopping");
      this.ws.send(JSON.stringify({ type: "stop" }));
    },

    /** 断点续跑：按旧运行记录重发，服务端复用已成功节点的输出，只重跑失败及下游 */
    async retry() {
      if (!this.wf || this.running || this.pendingCommand || !this.runId) return;
      const rid = this.runId;
      this.running = true;
      this.runStatus = "running";
      this.panel = "run";
      this.pendingCommand = "retry";
      await this.sendRunCommand({ type: "retry", run_id: rid });
    },

    async skipNode(nodeId: string, label: string, outputs: Record<string, any>, handle?: string) {
      if (!this.wf || !this.runId || this.stopping || this.pendingCommand || this.debugNodeId) return false;
      const live = this.running;
      this.pendingCommand = "skip";
      this.commandNodeKey = nodeId + (label ? `@${label}` : "");
      if (!live) { this.running = true; this.runStatus = "running"; }
      return this.sendRunCommand({ type: "skip", run_id: this.runId,
        node_key: this.commandNodeKey, outputs, ...(handle ? { handle } : {}) }, !live);
    },

    ingest(e: WfEvent) {
      if (e.run_id && this.runId && e.run_id !== this.runId && e.type !== "workflow_started"
          && e.type !== "workflow_node_debug") return;
      if (e.type === "node_delta" && e.node_id) {
        if (!this.running || this.stopping || ["manual", "stopped", "skipping"].includes(this.nodeRuns[e.node_id]?.status || "")) return;
        const r = this.nodeRuns[e.node_id] || (this.nodeRuns[e.node_id] = { status: "running" });
        r.stream = e.reset ? "" : (r.stream || "") + (e.text || "");
        r.reasoning = e.reset ? "" : (r.reasoning || "") + (e.reasoning || "");
        r.touched = Date.now();
        return;
      }
      if (e.live) return;
      switch (e.type) {
        case "workflow_started":
          this.resetRun();
          this.runId = e.run_id || "";
          this.running = true;
          this.runStatus = "running";
          break;
        case "workflow_stopping":
          this.stopping = true;
          this.finishNodes("stopping");
          break;
        case "workflow_node": {
          const id = e.node_id!;
          if (this.pendingCommand === "debug" && this.debugNodeId === id) this.pendingCommand = "";
          const prev = this.nodeRuns[id];
          const next: NodeRun = { ...prev, status: e.status!, label: e.label, touched: Date.now() };
          if (e.status === "running") {
            next.stream = "";
            next.reasoning = "";
            if (e.label) next.iterations = (prev?.iterations || 0) + 1;
          } else {
            next.duration_ms = e.duration_ms;
            next.outputs = e.outputs;
            next.error = e.error;
          }
          // 容器内多轮执行：skipped 不覆盖已成功的状态
          if (!(e.status === "skipped" && prev && prev.status === "succeeded")) this.nodeRuns[id] = next;
          if (e.status === "manual" && this.commandNodeKey === id + (e.label ? `@${e.label}` : "")) {
            this.pendingCommand = "";
            this.commandNodeKey = "";
          }
          break;
        }
        case "workflow_log":
          this.logs.push(e);
          if (e.node_id && this.nodeRuns[e.node_id]) this.nodeRuns[e.node_id].touched = Date.now();
          break;
        case "workflow_answer":
          this.runAnswers.push(e.text || "");
          break;
        case "workflow_finished":
          this.running = false;
          this.stopping = false;
          this.pendingCommand = "";
          this.commandNodeKey = "";
          this.finishNodes("stopped");
          this.runStatus = e.status as any;
          this.runError = e.error || "";
          this.runOutputs = e.outputs || {};
          this.runAnswers = e.answers || this.runAnswers;
          this.runDuration = e.duration_ms || 0;
          this.loadHistory();
          break;
        case "workflow_node_debug":
          this.running = false;
          this.stopping = false;
          this.pendingCommand = "";
          this.debugNodeId = "";
          this.runId = "";
          this.runStatus = e.status as any;
          this.finishNodes("stopped");
          if (e.node_id) {
            this.nodeRuns[e.node_id] = { status: e.status || "failed",
              outputs: e.outputs, error: e.error };
          }
          break;
        case "workflow_command_error":
          if (e.command === "skip" && e.node_key && this.commandNodeKey && e.node_key !== this.commandNodeKey) break;
          const pending = this.pendingCommand;
          this.pendingCommand = "";
          this.commandNodeKey = "";
          this.runError = e.detail || "请求未执行";
          if (["run", "debug", "retry"].includes(pending)
              || pending === "skip" && !["running", "skipping", "stopping"].some(s => (Object.values(this.nodeRuns) as NodeRun[]).some(r => r.status === s))) {
            this.running = false;
            this.debugNodeId = "";
            this.runStatus = "failed";
            this.finishNodes("stopped");
          }
          break;
        case "workflow_error":
          this.running = false;
          this.stopping = false;
          this.pendingCommand = "";
          this.commandNodeKey = "";
          this.finishNodes("stopped");
          this.runStatus = "failed";
          this.runError = e.detail || "";
          break;
      }
    },

    async loadHistory() {
      if (!this.wf) return;
      try { this.history = await listRuns(this.wf.id); } catch { this.history = []; }
    },

    /** force=true 强制重拉（进工作流页时用）：工具页的启用/禁用、工坊新建
     *  才能反映到工具节点下拉。失败时保留旧列表，不清空。 */
    async loadTools(force = false) {
      if (this.toolsLoaded && !force) return;
      try { this.tools = await getToolCards(); } catch { if (!this.toolsLoaded) this.tools = []; }
      this.toolsLoaded = true;
    },
  },
});
