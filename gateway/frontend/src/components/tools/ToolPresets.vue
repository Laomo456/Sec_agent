<script setup lang="ts">
/** 工具集预设：左侧列表，右侧编辑（名称/说明/勾选工具/附加用法说明），设默认。 */
import { NButton, NCheckbox, NInput, NPopconfirm, NRadio } from "naive-ui";
import { computed, ref } from "vue";
import { useToolsStore } from "../../stores/tools";
import type { ToolPreset } from "../../types";
import { dangerMeta } from "./toolMeta";

const store = useToolsStore();
const selId = ref(store.defaultPreset || "all");
const sel = computed<ToolPreset | undefined>(() => store.presets.find((p) => p.id === selId.value));
const allMode = computed(() => !!sel.value?.tools.includes("*"));

function add() {
  const id = `ts_${Math.random().toString(36).slice(2, 10)}`;
  store.presets.push({ id, name: "新工具集", description: "", tools: store.tools.filter((t) => t.danger === "readonly").map((t) => t.name), system: "" });
  selId.value = id;
}
function dup(p: ToolPreset) {
  const id = `ts_${Math.random().toString(36).slice(2, 10)}`;
  store.presets.push({ ...JSON.parse(JSON.stringify(p)), id, name: `${p.name} 副本`, builtin: false,
    tools: p.tools.includes("*") ? store.tools.map((t) => t.name) : [...p.tools] });
  selId.value = id;
}
function remove(p: ToolPreset) {
  store.presets = store.presets.filter((x) => x.id !== p.id);
  if (store.defaultPreset === p.id) store.defaultPreset = "all";
  selId.value = "all";
}
function toggleTool(name: string, on: boolean) {
  if (!sel.value) return;
  const s = new Set(sel.value.tools);
  on ? s.add(name) : s.delete(name);
  sel.value.tools = [...s];
}
function setAll(on: boolean) {
  if (!sel.value) return;
  sel.value.tools = on ? ["*"] : store.tools.map((t) => t.name);
}
function pickBy(fn: (d?: string) => boolean) {
  if (!sel.value) return;
  sel.value.tools = store.tools.filter((t) => fn(t.danger)).map((t) => t.name);
}
const groups = computed(() => {
  const m = new Map<string, typeof store.tools>();
  for (const t of store.tools) m.set(t.category || "misc", [...(m.get(t.category || "misc") || []), t]);
  return [...m.entries()];
});
</script>

<template>
  <div class="pre">
    <div class="list">
      <TransitionGroup name="row" tag="div" class="rows">
        <div v-for="p in store.presets" :key="p.id" class="row" :class="{ on: selId === p.id }"
             role="button" tabindex="0" @click="selId = p.id" @keydown.enter="selId = p.id">
          <div class="rt">
            <span class="rn">{{ p.name }}</span>
            <span v-if="store.defaultPreset === p.id" class="def">默认</span>
            <span v-if="p.builtin" class="bi">内置</span>
          </div>
          <div class="rd">{{ p.description || "—" }}</div>
          <div class="rc">
            <template v-if="p.tools.includes('*')">全部 {{ store.resolve(p).length }} 个工具</template>
            <template v-else>{{ store.resolve(p).length }} 个工具</template>
          </div>
        </div>
      </TransitionGroup>
      <button class="add" @click="add">＋ 新建工具集</button>
      <p class="hint">对话输入框里可以按任务切换工具集。全局禁用的工具在任何工具集里都不可用。</p>
    </div>

    <div v-if="sel" class="edit glass">
      <div class="eh">
        <n-input v-model:value="sel.name" :disabled="sel.builtin" size="large" class="en" placeholder="工具集名称" />
        <n-radio :checked="store.defaultPreset === sel.id" @update:checked="store.defaultPreset = sel!.id">设为默认</n-radio>
      </div>
      <n-input v-model:value="sel.description" :disabled="sel.builtin" placeholder="一句话说明这个工具集的用途" />

      <div class="sec">
        <span>包含的工具</span>
        <template v-if="!sel.builtin">
          <button class="qk" :class="{ on: allMode }" @click="setAll(!allMode)">{{ allMode ? "✓ 不限（含以后新增）" : "不限" }}</button>
          <button class="qk" @click="pickBy((d) => d === 'readonly')">只选只读</button>
          <button class="qk" @click="pickBy(() => false)">清空</button>
        </template>
      </div>
      <div v-for="[c, list] in groups" :key="c" class="tg">
        <div class="tgt">{{ c }}</div>
        <div class="tools">
          <label v-for="t in list" :key="t.name" class="tl" :class="{ dis: !t.enabled }"
                 :title="t.enabled ? t.description : '已全局禁用'">
            <n-checkbox :checked="allMode || sel.tools.includes(t.name)" :disabled="sel.builtin || allMode || !t.enabled"
                        @update:checked="(v: boolean) => toggleTool(t.name, v)" />
            <span class="dot" :style="{ background: dangerMeta(t.danger).color }" />
            <span class="tn">{{ t.name }}</span>
            <span class="td">{{ t.description }}</span>
          </label>
        </div>
      </div>

      <div class="sec"><span>附加用法说明</span><span class="sub">选这个工具集时追加给模型，可写工具的使用顺序、约束</span></div>
      <n-input v-model:value="sel.system" :disabled="sel.builtin" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }"
               placeholder="例如：先用 file_list 看清目录结构再动手；所有写操作前先 file_read 确认原文。" />

      <div class="ef">
        <n-button size="small" secondary @click="dup(sel)">复制为新工具集</n-button>
        <n-popconfirm v-if="!sel.builtin" @positive-click="remove(sel)">
          <template #trigger><n-button size="small" type="error" quaternary>删除</n-button></template>
          删除工具集「{{ sel.name }}」？
        </n-popconfirm>
        <span class="sp" />
        <span class="hint">改完点右上角「保存配置」</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pre { display: grid; grid-template-columns: 300px 1fr; gap: 18px; max-width: 1240px; align-items: start; }
.rows { display: flex; flex-direction: column; gap: 8px; }
.row { padding: 12px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--glass); cursor: pointer; transition: all 0.2s; }
.row:hover { border-color: var(--line-2); }
.row.on { border-color: rgba(143, 124, 255, 0.55); background: linear-gradient(135deg, rgba(143,124,255,0.14), rgba(45,212,191,0.04)); }
.row:focus-visible { outline: 2px solid var(--accent); }
.rt { display: flex; align-items: center; gap: 7px; }
.rn { font-size: 13.5px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.def { font-size: 10px; color: var(--accent-2); border: 1px solid rgba(45,212,191,0.4); border-radius: 999px; padding: 0 7px; }
.bi { font-size: 10px; color: var(--text-3); border: 1px solid var(--line-2); border-radius: 999px; padding: 0 7px; }
.rd { font-size: 11.5px; color: var(--text-3); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rc { font: 10.5px var(--mono); color: var(--text-2); margin-top: 6px; }
.add { width: 100%; margin-top: 10px; border: 1px dashed var(--line-2); background: none; color: var(--text-2); border-radius: 12px; padding: 9px; cursor: pointer; font-size: 12.5px; }
.add:hover { border-color: var(--accent); color: var(--text); }
.hint { font-size: 11.5px; color: var(--text-3); line-height: 1.6; }

.edit { border-radius: 20px; padding: 20px; display: flex; flex-direction: column; gap: 12px; animation: rise 0.3s var(--ease); }
.eh { display: flex; align-items: center; gap: 14px; }
.en { flex: 1; }
.sec { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 12.5px; font-weight: 600; color: var(--text-2); }
.sec .sub { font-weight: 400; font-size: 11px; color: var(--text-3); }
.qk { border: 1px solid var(--line); background: none; color: var(--text-2); border-radius: 999px; padding: 2px 10px; font-size: 11px; cursor: pointer; }
.qk:hover { border-color: var(--line-2); color: var(--text); }
.qk.on { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.tgt { font-size: 10.5px; color: var(--text-3); letter-spacing: 1px; font-weight: 600; margin: 4px 0; text-transform: uppercase; }
.tools { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 4px; }
.tl { display: flex; align-items: center; gap: 9px; padding: 7px 10px; border-radius: 10px; cursor: pointer; min-width: 0; }
.tl:hover { background: var(--glass-hover); }
.tl.dis { opacity: 0.4; cursor: not-allowed; }
.dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.tn { font: 12.5px var(--mono); flex-shrink: 0; }
.td { font-size: 11.5px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ef { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.sp { flex: 1; }
.row-enter-active, .row-leave-active { transition: all 0.25s var(--ease); }
.row-enter-from, .row-leave-to { opacity: 0; transform: translateX(-8px); }
@media (max-width: 768px) { .pre { grid-template-columns: 1fr; } }
</style>
