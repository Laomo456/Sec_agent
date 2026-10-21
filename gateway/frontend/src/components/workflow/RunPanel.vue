<script setup lang="ts">
/** 运行面板：填写开始节点的输入 → 运行；实时显示节点轨迹、回复、结果；历史运行回看。 */
import MarkdownIt from "markdown-it";
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTabs, NTabPane, NModal } from "naive-ui";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { getRun, uploadWorkflowFile } from "../../api";
import { useWorkflowStore } from "../../stores/workflow";
import { NODE_DEFS, handlesOf } from "./nodeDefs";
import type { WfNode } from "../../types";

const store = useWorkflowStore();
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const inputs = ref<Record<string, any>>({});
const tab = ref<"input" | "result" | "trace" | "history">("input");
const uploading = ref(false);
const uploadError = ref("");
const exporting = ref(false);

const vars = computed(() => store.startNode?.data.variables || []);
watch(() => store.wf?.id, () => {
  inputs.value = Object.fromEntries(vars.value.map((v: any) => [v.name, v.default ?? (v.type === "boolean" ? false : "")]));
  uploadError.value = "";
  tab.value = "input";
}, { immediate: true });
watch(() => store.runStatus, (s) => { if (s === "running") tab.value = "trace"; if (s && s !== "running") tab.value = "result"; });

const missing = computed(() => vars.value.filter((v: any) => v.required && !String(inputs.value[v.name] ?? "").trim()));
function start() {
  if (missing.value.length || uploading.value || store.running) return;
  store.run({ ...inputs.value });
}

async function upload(name: string, file?: File) {
  if (!file || !store.wf || store.running || uploading.value) return;
  const id = store.wf.id;
  uploading.value = true;
  uploadError.value = "";
  try {
    const result = await uploadWorkflowFile(id, file);
    if (store.wf?.id !== id) return;
    inputs.value[name] = result.path;
    uploading.value = false;
    if (store.startNode?.data.auto_run_on_upload && !missing.value.length) start();
  } catch (error: any) {
    uploadError.value = error?.message || "文件上传失败";
  } finally {
    uploading.value = false;
  }
}

function pickFile(name: string, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  void upload(name, file);
}

function dropFile(name: string, event: DragEvent) {
  const files = event.dataTransfer?.files;
  if (files && files.length > 1) { uploadError.value = "每次处理一个文件"; return; }
  void upload(name, files?.[0]);
}

const trace = computed(() => Object.entries(store.nodeRuns).map(([id, r]) => {
  const n = store.wf?.nodes.find((x) => x.id === id);
  return { id, r, title: n?.data.title || id, def: n ? NODE_DEFS[n.type] : null };
}).filter((x) => x.def && !x.def.hidden && !x.id.endsWith("_start")));
const answersHtml = computed(() => store.runAnswers.map((a) => md.render(a)));
const pretty = (v: any) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
const fmt = (ms?: number) => (ms == null ? "" : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`);
const statusText: Record<string, string> = { running: "运行中", succeeded: "成功", failed: "失败", stopped: "已停止", skipped: "跳过", exception: "异常", cached: "缓存" };
const outcomeText: Record<string, string> = { unpacked: "已解壳", dumped_unverified: "待验证", needs_manual: "需人工处理",
  unsupported: "暂不支持", no_packing_detected: "未发现壳", invalid_input: "输入无效", failed: "处理失败", cancelled: "已停止" };
const resultStatus = computed(() => store.stopping ? "停止中…" : store.runStatus === "succeeded" ? outcomeText[store.runOutputs.outcome] || statusText.succeeded : statusText[store.runStatus]);
const resultClass = computed(() => store.runStatus === "succeeded" && store.runOutputs.outcome && store.runOutputs.outcome !== "unpacked" ? "stopped" : store.runStatus);

/* ── 卡住检测：节点运行中但 2 分钟没有任何动静（流式/状态/日志都算） ──
   只提示不自动杀：oneforall 跑 4 分钟、ncat 空闲等待都是合法静默。 */
const now = ref(Date.now());
let tick: number | undefined;
onMounted(() => { tick = window.setInterval(() => { now.value = Date.now(); }, 5000); });
onUnmounted(() => window.clearInterval(tick));
const isStuck = (x: any) => store.running && x.r.status === "running"
  && !!x.r.touched && now.value - x.r.touched > 120_000;

/* ── 断点重试：终态运行可重发；卡住先停止（运行中的旧协程还占着状态，不能直接重发） ── */
const canRetry = computed(() => !!store.runId && !store.debugNodeId && !store.stopping
  && (["failed", "stopped"].includes(store.runStatus) || store.pendingCommand === "retry"));
const canRetryNode = (x: any) => canRetry.value && ["failed", "exception", "stopped", "running"].includes(x.r.status);
const canSkipNode = (x: any) => !!store.runId && !store.debugNodeId && !store.stopping
  && !["start", "end", "answer", "assigner", "note"].includes(store.wf?.nodes.find(n => n.id === x.id)?.type || "")
  && (store.running ? ["running", "skipping"].includes(x.r.status)
    : ["failed", "exception", "stopped", "running"].includes(x.r.status));

const skipDialog = ref(false);
const skipLoading = ref(false);
const skipError = ref("");
const skipTarget = ref<{ id: string; label: string; title: string; node: WfNode | null } | null>(null);
const skipMode = ref("text");
const skipText = ref("");
const skipJson = ref("{}");
const skipBase = ref<Record<string, any>>({});
const skipField = ref("text");
const skipHandle = ref<string>();
const skipHandleOptions = computed(() => skipTarget.value?.node ? handlesOf(skipTarget.value.node)
  .map(h => ({ label: h.label || "下一步", value: h.id })) : []);
const skipNeedsHandle = computed(() => ["if_else", "classifier"].includes(skipTarget.value?.node?.type || "")
  || skipHandleOptions.value.length > 1);
let skipVersion = 0;

function setSkipResult(outputs: Record<string, any>) {
  skipBase.value = structuredClone(outputs);
  skipJson.value = JSON.stringify(outputs, null, 2);
  skipText.value = typeof outputs[skipField.value] === "string" ? outputs[skipField.value] : "";
  if (outputs[skipField.value] !== undefined && typeof outputs[skipField.value] !== "string") skipMode.value = "json";
}

async function openSkip(x: any) {
  const version = ++skipVersion;
  const node = store.wf?.nodes.find(n => n.id === x.id) || null;
  skipTarget.value = { id: x.id, label: x.r.label || "", title: x.title, node };
  skipMode.value = "text";
  skipError.value = "";
  skipDialog.value = true;
  skipLoading.value = true;
  const vars = node ? NODE_DEFS[node.type]?.vars(node.data).filter(v => !v.desc?.includes("容器内")) || [] : [];
  skipField.value = vars.find(v => v.type === "string")?.name || vars[0]?.name || "text";
  const defaults = Object.fromEntries(vars.map(v => [v.name,
    v.type === "string" ? "" : v.type === "array" ? [] : v.type === "number" ? 0 : v.type === "boolean" ? false : null]));
  setSkipResult({ ...defaults, ...(x.r.outputs || {}) });
  skipHandle.value = undefined;
  const key = x.id + (x.r.label ? `@${x.r.label}` : "");
  try {
    const current = await getRun(store.runId);
    if (version !== skipVersion || !skipDialog.value) return;
    const originalNode = current.graph?.nodes?.find((n: WfNode) => n.id === x.id);
    if (originalNode && skipTarget.value) skipTarget.value.node = originalNode;
    await store.loadHistory();
    for (const h of store.history.slice(0, 20)) {
      const run = await getRun(h.id);
      if (version !== skipVersion || !skipDialog.value) return;
      const rec = run.nodes?.[key];
      if (rec && Object.prototype.hasOwnProperty.call(rec, "outputs")) {
        setSkipResult({ ...defaults, ...rec.outputs });
        skipHandle.value = rec.handles?.[0] || rec.outputs?.result || rec.outputs?.class_id;
        break;
      }
    }
  } catch { /* 历史不可用时保持可编辑的空输出 */ }
  finally { if (version === skipVersion) skipLoading.value = false; }
}

async function submitSkip() {
  if (!skipTarget.value || skipLoading.value) return;
  skipError.value = "";
  let outputs: Record<string, any>;
  try {
    outputs = skipMode.value === "json" ? JSON.parse(skipJson.value)
      : { ...skipBase.value, [skipField.value]: skipText.value };
    if (!outputs || Array.isArray(outputs) || typeof outputs !== "object") throw new Error("请填写 JSON 输出对象，字段值可以为空或 null");
    if (skipNeedsHandle.value && !skipHandle.value) throw new Error("请选择继续分支");
  } catch (e: any) { skipError.value = e.message; return; }
  const ok = await store.skipNode(skipTarget.value.id, skipTarget.value.label, outputs, skipHandle.value);
  if (ok) { skipDialog.value = false; tab.value = "trace"; }
  else skipError.value = store.runError || "当前节点暂时不能跳过";
}

/* 历史：点开载入该次运行的节点状态到画布 */
async function loadRun(id: string) {
  if (store.running || store.pendingCommand) return;
  const run = await getRun(id);
  store.resetRun();
  store.runId = run.id;
  store.runStatus = run.status;
  store.runError = run.error || "";
  store.runOutputs = run.outputs || {};
  store.runAnswers = run.answers || [];
  store.runDuration = run.duration_ms || 0;
  for (const [key, rec] of Object.entries<any>(run.nodes || {})) {
    const id2 = key.split("@")[0];
    store.nodeRuns[id2] = { status: rec.status, label: rec.label || "", duration_ms: rec.duration_ms, outputs: rec.outputs, error: rec.error };
  }
  store.logs = (run.logs || []).map((l: any) => ({ ...l, type: "workflow_log" }));
  inputs.value = { ...(run.inputs || {}) };
  tab.value = "result";
}

/* ── 导出：Markdown 报告 / 完整运行 JSON ── */
const exportable = computed(() => !!store.runStatus && store.runStatus !== "running"
  && (store.runAnswers.length > 0 || Object.keys(store.runOutputs).length > 0));

function download(name: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** 内容含 ``` 时升级为四反引号围栏，避免把导出文件撑破 */
const fence = (body: string) => {
  const tick = "`".repeat(body.includes("```") ? 4 : 3);
  return `${tick}\n${body}\n${tick}`;
};
const safeName = (s: string) => (s || "workflow").replace(/[\\/:*?"<>|]/g, "_");

function exportMd() {
  const lines: string[] = [`# ${store.wf?.name || "工作流"} — 运行结果`, ""];
  lines.push(`- 状态：${resultStatus.value} · 耗时 ${fmt(store.runDuration) || "-"} · 运行ID ${store.runId || "-"}`);
  if (store.runError) lines.push(`- 错误：${store.runError}`);
  if (store.runAnswers.length) {
    lines.push("", "## 回答", "");
    for (const a of store.runAnswers) lines.push(a, "", "---", "");
  }
  if (Object.keys(store.runOutputs).length) {
    lines.push("", "## 输出", "");
    for (const [k, v] of Object.entries(store.runOutputs)) lines.push(`### ${k}`, "", fence(pretty(v)), "");
  }
  if (trace.value.length) {
    lines.push("", "## 节点轨迹", "");
    for (const x of trace.value) {
      lines.push(`### ${x.title}（${x.id}）— ${statusText[x.r.status] || x.r.status}${x.r.duration_ms != null ? ` · ${fmt(x.r.duration_ms)}` : ""}`);
      if (x.r.error) lines.push("", `> 错误：${x.r.error}`);
      for (const [k, v] of Object.entries(x.r.outputs || {})) lines.push("", `**${k}**`, "", fence(pretty(v)));
      lines.push("");
    }
  }
  download(`${safeName(store.wf?.name)}_${store.runId || Date.now()}.md`, lines.join("\n"), "text/markdown");
}

async function exportJson() {
  /* 有 runId 就导服务端完整记录（含 inputs/logs/逐节点明细），否则退回本地状态 */
  exporting.value = true;
  try {
    const run = store.runId ? await getRun(store.runId).catch(() => null) : null;
    const data = run || { id: store.runId, status: store.runStatus, error: store.runError,
      outputs: store.runOutputs, answers: store.runAnswers, duration_ms: store.runDuration,
      inputs: inputs.value, nodes: store.nodeRuns };
    download(`${safeName(store.wf?.name)}_${store.runId || Date.now()}.json`,
      JSON.stringify(data, null, 2), "application/json");
  } finally { exporting.value = false; }
}
</script>

<template>
  <aside class="rp glass">
    <header>
      <span class="t">运行</span>
      <span v-if="store.runStatus" class="st" :class="resultClass">
        <i v-if="store.runStatus === 'running' && !store.stopping" class="spin" />{{ resultStatus }}
        <template v-if="store.runDuration"> · {{ fmt(store.runDuration) }}</template>
      </span>
      <n-button v-if="store.running" size="tiny" type="error" secondary :loading="store.stopping"
                :disabled="store.stopping || !!store.pendingCommand && store.pendingCommand !== 'skip'" @click="store.stop()">
        {{ store.stopping ? "停止中" : "停止" }}
      </n-button>
      <button class="x" aria-label="关闭" @click="store.panel = ''">
        <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg></button>
    </header>

    <n-tabs v-model:value="tab" type="segment" size="small" class="tabs" animated>
      <n-tab-pane name="input" tab="输入">
        <div class="pane">
          <div v-if="!vars.length" class="hint">开始节点没有定义输入变量，直接运行即可</div>
          <label v-for="v in vars" :key="v.name" class="field">
            <span class="lab">{{ v.label || v.name }} <span v-if="v.required" class="req">*</span><code>{{ v.name }}</code></span>
            <n-input-number v-if="v.type === 'number'" v-model:value="inputs[v.name]" size="small" :min="v.min" :max="v.max" />
            <n-switch v-else-if="v.type === 'boolean'" v-model:value="inputs[v.name]" size="small" :disabled="store.running" />
            <div v-else-if="v.type === 'file'" class="file-input" @dragover.prevent @drop.prevent="dropFile(v.name, $event)">
              <n-input v-model:value="inputs[v.name]" size="small" placeholder="本机文件路径" :disabled="store.running || uploading" />
              <input type="file" :aria-label="v.label || v.name" :disabled="store.running || uploading" @change="pickFile(v.name, $event)" />
            </div>
            <n-select v-else-if="v.type === 'select'" v-model:value="inputs[v.name]" size="small"
                      :options="(v.options || []).map((o: string) => ({ label: v.labels?.[o] || o, value: o }))" />
            <n-input v-else v-model:value="inputs[v.name]" :type="v.type === 'paragraph' ? 'textarea' : 'text'"
                     :autosize="v.type === 'paragraph' ? { minRows: 3, maxRows: 10 } : undefined" size="small"
                     @keydown.ctrl.enter="start" />
          </label>
          <div class="acts">
            <n-button v-if="store.running" type="error" secondary block :loading="store.stopping"
                      :disabled="store.stopping || !!store.pendingCommand && store.pendingCommand !== 'skip'" @click="store.stop()">{{ store.stopping ? "停止中…" : "停止运行" }}</n-button>
            <n-button v-else type="primary" block :loading="uploading || store.pendingCommand === 'run'" :disabled="missing.length > 0 || uploading || !!store.pendingCommand" @click="start">
              ▶ 开始运行 <span class="kbd">Ctrl+Enter</span>
            </n-button>
            <div v-if="missing.length" class="hint">请填写：{{ missing.map((v: any) => v.label || v.name).join("、") }}</div>
            <div v-if="uploadError" class="err">{{ uploadError }}</div>
          </div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="result" tab="结果">
        <div class="pane">
          <div v-if="!store.runStatus" class="hint center">还没有运行结果</div>
          <div v-if="store.runError" class="err">{{ store.runError }}</div>
          <div v-if="canRetry" class="exrow">
            <n-button size="small" type="primary" secondary :loading="store.pendingCommand === 'retry'"
                      :disabled="!!store.pendingCommand" @click="store.retry()">↻ 重试（复用已成功节点）</n-button>
            <span class="hint">沿用当时的画布与输入，只重跑失败节点及其下游</span>
          </div>
          <div v-if="exportable" class="exrow">
            <n-button size="tiny" secondary @click="exportMd">⬇ 导出 Markdown</n-button>
            <n-button size="tiny" secondary :loading="exporting" @click="exportJson">⬇ 导出 JSON</n-button>
          </div>
          <div v-for="(h, i) in answersHtml" :key="i" class="answer md" v-html="h" />
          <template v-if="Object.keys(store.runOutputs).length">
            <div class="sub">输出</div>
            <div v-for="(v, k) in store.runOutputs" :key="k" class="kv">
              <code>{{ k }}</code><pre>{{ pretty(v) }}</pre>
            </div>
          </template>
        </div>
      </n-tab-pane>

      <n-tab-pane name="trace" tab="追踪">
        <div class="pane">
          <div v-if="!trace.length" class="hint center">运行后在这里查看每个节点的执行情况</div>
          <div v-if="store.runError" class="err">{{ store.runError }}</div>
          <TransitionGroup name="tr" tag="div" class="trace">
            <details v-for="x in trace" :key="x.id" class="ti" :class="x.r.status" :style="{ '--c': x.def!.color }">
              <summary>
                <span class="ic"><svg viewBox="0 0 24 24"><path :d="x.def!.icon" /></svg></span>
                <span class="nm">{{ x.title }}</span>
                <span v-if="x.r.iterations" class="it">×{{ x.r.iterations }}</span>
                <span class="d">{{ fmt(x.r.duration_ms) }}</span>
                <span v-if="isStuck(x)" class="stuckhint" title="2 分钟没有任何输出（长任务可能正常，如子域名收集）">疑似卡住</span>
                <button v-if="canRetryNode(x)" class="rb" :disabled="!!store.pendingCommand" title="断点重试：已成功节点直接复用，从这里继续跑" @click.stop.prevent="store.retry()">↻</button>
                <n-button v-if="canSkipNode(x)" size="tiny" secondary :loading="x.r.status === 'skipping' || store.commandNodeKey === x.id + (x.r.label ? '@' + x.r.label : '')"
                          :disabled="!!store.pendingCommand" @click.stop.prevent="openSkip(x)">跳过</n-button>
                <span class="node-status">{{ ({ ...statusText, manual: '手动结果', skipping: '跳过中', stopping: '停止中' })[x.r.status] }}</span>
                <span class="dot" />
              </summary>
              <div class="tb">
                <div v-if="isStuck(x)" class="hint">长时间无输出。可以停止后重试，或点“跳过”填写上次结果继续；空结果也可提交。</div>
                <div v-if="x.r.error" class="err">{{ x.r.error }}</div>
                <details v-if="x.r.reasoning && x.r.status === 'running'">
                  <summary>思考中</summary>
                  <pre>{{ x.r.reasoning }}</pre>
                </details>
                <pre v-if="x.r.stream && x.r.status === 'running'">{{ x.r.stream }}</pre>
                <div v-for="(v, k) in x.r.outputs || {}" :key="k" class="kv"><code>{{ k }}</code><pre>{{ pretty(v) }}</pre></div>
              </div>
            </details>
          </TransitionGroup>
          <template v-if="store.logs.length">
            <div class="sub">日志</div>
            <div v-for="(l, i) in store.logs" :key="i" class="log" :class="l.level">
              <code>{{ (l.ts || "").slice(11, 19) }}</code>{{ l.msg }}
            </div>
          </template>
        </div>
      </n-tab-pane>

      <n-tab-pane name="history" tab="历史">
        <div class="pane">
          <div v-if="!store.history.length" class="hint center">暂无运行记录</div>
          <button v-for="h in store.history" :key="h.id" class="hi" :class="{ on: h.id === store.runId }" :disabled="store.running || !!store.pendingCommand" @click="loadRun(h.id)">
            <span class="hd" :class="h.status" />
            <span class="hm"><span>{{ (h.started || "").replace("T", " ").slice(5, 19) }}</span>
              <span class="hq">{{ Object.values(h.inputs || {}).join(" · ").slice(0, 40) || "无输入" }}</span></span>
            <span class="d">{{ fmt(h.duration_ms) }}</span>
          </button>
        </div>
      </n-tab-pane>
    </n-tabs>
  </aside>
  <n-modal v-model:show="skipDialog" preset="card" title="跳过节点并填写结果" :style="{ width: 'min(620px, 92vw)' }"
           :mask-closable="false" :closable="!store.pendingCommand">
    <div class="pane">
      <div>{{ skipTarget?.title }} <span class="hint">{{ skipTarget?.label }}</span></div>
      <div class="hint">使用上次结果或填写替代结果，然后继续下游。没有文本时可以留空。</div>
      <n-select v-model:value="skipMode" :disabled="skipLoading" :options="[{ label: '文本结果', value: 'text' }, { label: '完整输出对象（JSON）', value: 'json' }]" />
      <label v-if="skipMode === 'text'" class="field">
        <span class="lab">{{ skipField }}</span>
        <n-input v-model:value="skipText" type="textarea" :disabled="skipLoading" :autosize="{ minRows: 5, maxRows: 14 }" placeholder="键入上次运行结果；没有文本可以留空" />
      </label>
      <n-input v-else v-model:value="skipJson" type="textarea" :disabled="skipLoading" :autosize="{ minRows: 5, maxRows: 14 }" placeholder="输出对象，例如 { &quot;text&quot;: &quot;&quot; }；也支持 {}" />
      <n-select v-if="skipNeedsHandle" v-model:value="skipHandle" :disabled="skipLoading" :options="skipHandleOptions" placeholder="选择继续分支" />
      <div v-if="skipError" class="err">{{ skipError }}</div>
      <div class="exrow">
        <n-button :disabled="!!store.pendingCommand" @click="skipDialog = false">取消</n-button>
        <n-button type="primary" :loading="skipLoading || store.pendingCommand === 'skip'"
                  :disabled="skipLoading || !!store.pendingCommand || store.stopping" @click="submitSkip">跳过并继续</n-button>
      </div>
    </div>
  </n-modal>
</template>

<style scoped>
.rp {
  position: absolute; top: 12px; right: 12px; bottom: 12px; width: 400px; z-index: 13;
  border-radius: 18px; background: rgba(12, 14, 24, 0.92); display: flex; flex-direction: column;
  box-shadow: var(--shadow); animation: slide-in 0.3s var(--ease);
}
@keyframes slide-in { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: none; } }
header { display: flex; align-items: center; gap: 10px; padding: 14px 14px 10px 18px; }
.t { font-size: 15px; font-weight: 600; }
.st { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; padding: 2px 10px; border-radius: 999px; border: 1px solid var(--line); }
.st.running { color: var(--accent); border-color: rgba(143,124,255,0.4); }
.st.succeeded { color: var(--ok); border-color: rgba(52,211,153,0.4); }
.st.failed { color: var(--err); border-color: rgba(248,113,113,0.4); }
.st.stopped { color: var(--warn); border-color: rgba(251,191,36,0.4); }
.spin { width: 10px; height: 10px; border-radius: 50%; border: 2px solid rgba(143,124,255,0.25); border-top-color: var(--accent); animation: spin 0.8s linear infinite; }
.x { margin-left: auto; border: 0; background: none; padding: 5px; border-radius: 8px; cursor: pointer; }
.x svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; }
.x:hover { background: var(--glass-hover); }
.tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 14px; }
.tabs :deep(.n-tabs-pane-wrapper) { flex: 1; min-height: 0; overflow-y: auto; }
.pane { display: flex; flex-direction: column; gap: 12px; padding: 12px 2px 20px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.file-input { display: flex; flex-direction: column; gap: 10px; padding: 10px; border: 1px dashed var(--line); border-radius: 6px; min-width: 0; }
.file-input input[type="file"] { width: 100%; min-width: 0; font: 12px var(--sans); color: var(--text-2); }
.file-input input[type="file"]::file-selector-button { margin-right: 8px; padding: 5px 8px; border: 1px solid var(--line); border-radius: 4px; background: var(--glass-hover); color: var(--text); cursor: pointer; }
.answer :deep(a) { overflow-wrap: anywhere; }
.lab { font-size: 12px; color: var(--text-2); display: flex; align-items: center; gap: 6px; }
.lab code { margin-left: auto; font: 10.5px var(--mono); color: var(--text-3); }
.req { color: var(--err); }
.acts { display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
.kbd { font: 10px var(--mono); opacity: 0.6; margin-left: 8px; }
.hint { font-size: 12px; color: var(--text-3); }
.hint.center { text-align: center; padding: 30px 0; }
.err { font-size: 12px; color: #fecaca; background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3); border-radius: 10px; padding: 9px 11px; white-space: pre-wrap; word-break: break-all; }
.exrow { display: flex; gap: 8px; flex-shrink: 0; }
.answer {
  font-size: 13.5px; line-height: 1.75; padding: 12px 14px; border-radius: 14px;
  background: linear-gradient(135deg, rgba(143,124,255,0.1), rgba(45,212,191,0.05)); border: 1px solid rgba(143,124,255,0.25);
  animation: rise 0.3s var(--ease);
}
.sub { font-size: 11px; color: var(--text-3); letter-spacing: 1px; font-weight: 600; margin-top: 4px; }
.kv { display: flex; flex-direction: column; gap: 4px; }
.kv code { font: 11.5px var(--mono); color: var(--accent-2); }
pre { margin: 0; font: 11.5px/1.55 var(--mono); background: rgba(3,4,10,0.6); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto; }
.trace { display: flex; flex-direction: column; gap: 6px; }
.ti { border: 1px solid var(--line); border-radius: 12px; background: rgba(255,255,255,0.02); overflow: hidden; }
.ti summary { display: flex; align-items: center; gap: 9px; padding: 8px 10px; cursor: pointer; list-style: none; }
.ti summary::-webkit-details-marker { display: none; }
.ti .ic { width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center; background: color-mix(in srgb, var(--c) 18%, transparent); }
.ti .ic svg { width: 12px; height: 12px; stroke: var(--c); stroke-width: 2.2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.nm { flex: 1; font-size: 12.5px; }
.it { font: 10.5px var(--mono); color: var(--warn); }
.d { font: 10.5px var(--mono); color: var(--text-3); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-3); }
.ti.running .dot { background: var(--accent); animation: pulse 1.4s infinite; }
.ti.succeeded .dot { background: var(--ok); }
.ti.failed .dot { background: var(--err); }
.ti.exception .dot { background: var(--warn); }
.ti.cached .dot { background: var(--accent-2); }
.ti.cached { opacity: 0.75; }
.ti.manual .dot { background: var(--warn); }
.node-status { font-size: 10px; color: var(--text-3); white-space: nowrap; }
.rb:disabled, .hi:disabled { cursor: default; opacity: 0.5; }
.ti.skipped { opacity: 0.5; }
.stuckhint { font-size: 10.5px; color: var(--warn); border: 1px solid rgba(251,191,36,0.4); border-radius: 999px; padding: 1px 7px; white-space: nowrap; }
.rb { flex-shrink: 0; width: 20px; height: 20px; border-radius: 6px; border: 1px solid var(--line); background: none; color: var(--accent-2); font-size: 12px; line-height: 1; cursor: pointer; }
.rb:hover { border-color: rgba(143,124,255,0.5); background: rgba(143,124,255,0.1); }
.link { color: var(--accent-2); cursor: pointer; text-decoration: underline; }
.tb { padding: 0 10px 10px; display: flex; flex-direction: column; gap: 8px; }
.tr-enter-active { transition: all 0.3s var(--ease); }
.tr-enter-from { opacity: 0; transform: translateX(10px); }
.log { font-size: 11.5px; color: var(--text-2); line-height: 1.6; word-break: break-all; }
.log code { font: 10.5px var(--mono); color: var(--text-3); margin-right: 7px; }
.log.warn { color: var(--warn); }
.hi {
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; border-radius: 11px;
  border: 1px solid var(--line); background: rgba(255,255,255,0.02); cursor: pointer; color: var(--text); text-align: left;
}
.hi:hover, .hi.on { border-color: rgba(143,124,255,0.4); background: rgba(143,124,255,0.08); }
.hd { width: 8px; height: 8px; border-radius: 50%; background: var(--text-3); flex-shrink: 0; }
.hd.succeeded { background: var(--ok); } .hd.failed { background: var(--err); } .hd.stopped { background: var(--warn); }
.hm { flex: 1; min-width: 0; display: flex; flex-direction: column; font: 11.5px var(--mono); }
.hq { font-family: var(--sans); font-size: 11px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 768px) {
  .rp { position: fixed; width: calc(100vw - 24px); max-width: calc(100vw - 24px); left: 12px; right: 12px; top: auto; bottom: 12px; height: 70%; box-sizing: border-box; }
  .field, .pane, .tabs { min-width: 0; }
  .rp :deep(.n-input), .rp :deep(.n-input-number), .rp :deep(.n-select) { max-width: 100%; }
}
</style>
