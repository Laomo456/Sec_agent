<script setup lang="ts">
/** 工具详情：说明、签名、参数表、示例、模型实际看到的工具卡，以及试运行。 */
import { NButton, NInput, NTabs, NTabPane, useMessage } from "naive-ui";
import { computed, ref } from "vue";
import { tryTool } from "../../api";
import type { ToolCard } from "../../types";
import { dangerMeta, kindLabel, paramRows, sampleParams, sourceLabel } from "./toolMeta";

const props = defineProps<{ tool: ToolCard }>();
const emit = defineEmits<{ (e: "close"): void; (e: "edit"): void }>();
const msg = useMessage();
const rows = computed(() => paramRows(props.tool.params_schema));
const dm = computed(() => dangerMeta(props.tool.danger));
const paramsText = ref(JSON.stringify(sampleParams(props.tool), null, 2));
const running = ref(false);
const result = ref<any>(null);
const tab = ref("doc");

/** 模型在检索时看到的紧凑卡（与 toolbox registry.card 同形） */
const modelCard = computed(() => {
  const c: Record<string, any> = { name: props.tool.name, sig: props.tool.sig, description: props.tool.description,
    category: props.tool.category, danger: props.tool.danger };
  if (props.tool.example && Object.keys(props.tool.example).length) c.example = props.tool.example;
  return JSON.stringify(c, null, 2);
});

async function run() {
  let params: any;
  try { params = JSON.parse(paramsText.value || "{}"); }
  catch { msg.error("参数不是合法 JSON"); return; }
  running.value = true;
  result.value = null;
  try { result.value = await tryTool(props.tool.name, params); }
  catch (e: any) { result.value = { status: "error", detail: e.message }; }
  finally { running.value = false; }
}
async function copy(text: string) {
  await navigator.clipboard.writeText(text);
  msg.success("已复制");
}
const resultText = computed(() => {
  const r = result.value;
  if (!r) return "";
  return r.output ?? [r.output_head, r.output_tail && `…\n${r.output_tail}`].filter(Boolean).join("\n")
    ?? r.detail ?? JSON.stringify(r, null, 2);
});
</script>

<template>
  <aside class="det glass">
    <header>
      <span class="ic" :style="{ '--c': dm.color }"><svg viewBox="0 0 24 24"><path :d="dm.icon" /></svg></span>
      <div class="hd">
        <div class="nm">{{ tool.name }}</div>
        <div class="meta">
          <span class="tag" :style="{ '--c': dm.color }" :title="dm.desc">{{ dm.label }}</span>
          <span class="tag muted">{{ sourceLabel(tool) }}</span>
          <span v-if="tool.kind" class="tag muted">{{ kindLabel[tool.kind] || tool.kind }}</span>
          <span v-if="tool.timeout" class="tag muted">超时 {{ tool.timeout }}s</span>
        </div>
      </div>
      <button class="x" aria-label="关闭详情" @click="emit('close')">
        <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg></button>
    </header>

    <p class="desc">{{ tool.description }}</p>
    <div class="sig" @click="copy(tool.sig || tool.name)" title="点击复制">
      <code>{{ tool.sig || tool.name }}</code>
    </div>

    <n-tabs v-model:value="tab" type="line" size="small" animated>
      <n-tab-pane name="doc" tab="说明">
        <div class="pane">
          <div v-if="rows.length" class="tbl">
            <div class="tr th"><span>参数</span><span>类型</span><span>说明</span></div>
            <div v-for="r in rows" :key="r.name" class="tr">
              <span class="pn">{{ r.name }}<i v-if="r.required">*</i></span>
              <span class="pt">{{ r.type }}</span>
              <span class="pd">{{ r.desc }}
                <span v-if="r.enum" class="en">可选：<code v-for="e in r.enum" :key="e">{{ e }}</code></span>
              </span>
            </div>
          </div>
          <div v-else class="muted-t">无参数</div>

          <template v-if="tool.usage">
            <div class="sub">用法要点</div>
            <div class="usage">{{ tool.usage }}</div>
          </template>
          <template v-if="tool.example && Object.keys(tool.example).length">
            <div class="sub">调用示例</div>
            <pre>{{ JSON.stringify(tool.example, null, 2) }}</pre>
          </template>
          <template v-if="tool.keywords?.length">
            <div class="sub">检索关键词</div>
            <div class="kws"><span v-for="k in tool.keywords" :key="k">{{ k }}</span></div>
          </template>
          <div v-if="tool.requires?.length" class="muted-t">依赖程序：{{ tool.requires.join("、") }}</div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="try" tab="试运行">
        <div class="pane">
          <div v-if="tool.danger !== 'readonly'" class="warn">⚠ 这是{{ dm.label }}工具，试运行会真实执行（工作目录：toolbox/workspace/tool-playground/files）</div>
          <n-input v-model:value="paramsText" type="textarea" class="mono" :autosize="{ minRows: 4, maxRows: 14 }"
                   placeholder="{ }" @keydown.ctrl.enter="run" />
          <n-button type="primary" :loading="running" @click="run">▶ 运行 <span class="k">Ctrl+Enter</span></n-button>
          <Transition name="fade">
            <div v-if="result" class="res" :class="result.status">
              <div class="rh">
                <span class="st">{{ result.status }}</span>
                <span v-if="result.exit_code" class="muted-t">exit {{ result.exit_code }}</span>
                <span v-if="result.stats" class="muted-t">{{ result.stats.duration_ms }}ms · ~{{ result.stats.tokens_est }} tok</span>
                <span v-if="result.stats?.truncated" class="trunc">已截断</span>
              </div>
              <div v-if="result.field" class="muted-t">字段 <code>{{ result.field }}</code>：{{ result.detail }}</div>
              <pre>{{ resultText }}</pre>
              <details><summary>模型收到的完整信封</summary><pre>{{ JSON.stringify(result, null, 2) }}</pre></details>
            </div>
          </Transition>
        </div>
      </n-tab-pane>

      <n-tab-pane name="card" tab="模型视角">
        <div class="pane">
          <div class="muted-t">两段式检索时，search_tools 返回的就是这张紧凑卡（签名代替整份 schema，约 40 token）；
            全量直连时模型拿到完整参数 schema。</div>
          <pre>{{ modelCard }}</pre>
          <div class="sub">完整参数 schema</div>
          <pre>{{ JSON.stringify(tool.params_schema || {}, null, 2) }}</pre>
        </div>
      </n-tab-pane>
    </n-tabs>

    <footer v-if="tool.editable">
      <n-button size="small" secondary block @click="emit('edit')">在工坊中编辑</n-button>
    </footer>
  </aside>
</template>

<style scoped>
.det { border-radius: 20px; padding: 18px; background: rgba(12, 14, 24, 0.88); box-shadow: var(--shadow);
  max-height: calc(100vh - 150px); overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
header { display: flex; gap: 12px; align-items: flex-start; }
.ic { width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center; flex-shrink: 0;
  background: color-mix(in srgb, var(--c) 16%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 35%, transparent); }
.ic svg { width: 18px; height: 18px; stroke: var(--c); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.hd { flex: 1; min-width: 0; }
.nm { font: 600 16px var(--mono); }
.meta { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 5px; }
.tag { font-size: 10.5px; padding: 1px 8px; border-radius: 999px; color: var(--c); border: 1px solid color-mix(in srgb, var(--c) 35%, transparent); }
.tag.muted { --c: var(--text-3); }
.x { border: 0; background: none; padding: 5px; border-radius: 8px; cursor: pointer; }
.x svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; }
.x:hover { background: var(--glass-hover); }
.desc { margin: 0; font-size: 13px; color: var(--text-2); line-height: 1.6; }
.sig { padding: 9px 12px; border-radius: 10px; background: rgba(143, 124, 255, 0.08); border: 1px solid rgba(143, 124, 255, 0.25); cursor: copy; }
.sig code { font: 12px/1.6 var(--mono); color: #d6d0ff; white-space: pre-wrap; word-break: break-word; }
.pane { display: flex; flex-direction: column; gap: 10px; padding: 10px 0 6px; }
.tbl { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.tr { display: grid; grid-template-columns: 100px 70px 1fr; gap: 8px; padding: 8px 10px; font-size: 12px; border-top: 1px solid var(--line); }
.tr.th { border-top: 0; background: rgba(255, 255, 255, 0.03); color: var(--text-3); font-size: 11px; }
.pn { font-family: var(--mono); color: var(--text); word-break: break-all; }
.pn i { color: var(--err); font-style: normal; margin-left: 2px; }
.pt { font-family: var(--mono); color: var(--accent-2); font-size: 11px; }
.pd { color: var(--text-2); line-height: 1.5; }
.en { display: block; margin-top: 3px; color: var(--text-3); }
.en code { font: 10.5px var(--mono); background: rgba(255,255,255,0.05); border-radius: 4px; padding: 0 4px; margin: 0 2px; }
.sub { font-size: 11px; color: var(--text-3); font-weight: 600; letter-spacing: 1px; margin-top: 4px; }
.usage { font-size: 12.5px; color: var(--text-2); line-height: 1.7; white-space: pre-wrap; }
pre { margin: 0; font: 11.5px/1.55 var(--mono); background: rgba(3, 4, 10, 0.6); border: 1px solid var(--line); border-radius: 10px;
  padding: 9px 11px; white-space: pre-wrap; word-break: break-all; max-height: 320px; overflow: auto; }
.kws { display: flex; flex-wrap: wrap; gap: 5px; }
.kws span { font-size: 11px; background: rgba(255,255,255,0.05); border: 1px solid var(--line); border-radius: 6px; padding: 1px 7px; color: var(--text-2); }
.muted-t { font-size: 11.5px; color: var(--text-3); line-height: 1.55; }
.warn { font-size: 12px; color: #fde68a; background: rgba(251, 191, 36, 0.08); border: 1px solid rgba(251, 191, 36, 0.25); border-radius: 10px; padding: 8px 11px; }
.mono :deep(textarea) { font-family: var(--mono) !important; font-size: 12px !important; }
.k { font: 10px var(--mono); opacity: 0.55; margin-left: 6px; }
.res { border-radius: 12px; border: 1px solid var(--line); padding: 10px; display: flex; flex-direction: column; gap: 7px; animation: rise 0.3s var(--ease); }
.res.ok { border-color: rgba(52, 211, 153, 0.35); }
.res.error, .res.timeout, .res.validation_error, .res.unknown_tool { border-color: rgba(248, 113, 113, 0.4); }
.rh { display: flex; align-items: center; gap: 10px; }
.st { font: 600 11px var(--mono); padding: 1px 8px; border-radius: 6px; background: rgba(255,255,255,0.06); }
.res.ok .st { color: var(--ok); }
.trunc { font-size: 10.5px; color: var(--warn); }
details summary { font-size: 11.5px; color: var(--text-3); cursor: pointer; margin-bottom: 6px; }
footer { margin-top: auto; padding-top: 6px; }
.fade-enter-active { transition: opacity 0.2s; }
.fade-enter-from { opacity: 0; }
</style>
