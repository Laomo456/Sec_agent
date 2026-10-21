<script setup lang="ts">
/** 可插入变量的输入框：右上角「{x}」按钮或键入 {{ 弹出上游变量列表。
 *  mode=ref 时整个值就是一个变量引用（用于迭代输入、条件左值等）。 */
import { NPopover } from "naive-ui";
import { computed, nextTick, ref } from "vue";
import { useWorkflowStore } from "../../stores/workflow";

const props = withDefaults(defineProps<{
  modelValue: string; nodeId: string; placeholder?: string; rows?: number;
  mode?: "text" | "ref"; mono?: boolean;
}>(), { rows: 1, mode: "text" });
const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();
const store = useWorkflowStore();
const show = ref(false);
const el = ref<HTMLTextAreaElement | HTMLInputElement>();
const filter = ref("");

const groups = computed(() => {
  const kw = filter.value.toLowerCase();
  return store.upstreamVars(props.nodeId)
    .map((g) => ({ ...g, vars: g.vars.filter((v) => !kw || v.name.toLowerCase().includes(kw) || g.title.toLowerCase().includes(kw)) }))
    .filter((g) => g.vars.length);
});

/** 把 {{node.var}} 渲染成彩色 chip 预览（只读展示层） */
const titleOf = (nid: string) => nid === "sys" ? "sys" : nid === "env" ? "env"
  : store.wf?.nodes.find((n) => n.id === nid)?.data.title || nid;
const refDisplay = computed(() => {
  const m = (props.modelValue || "").match(/^\{\{\s*([\w-]+)\.([\w.\[\]-]+)\s*\}\}$/);
  return m ? { node: titleOf(m[1]), v: m[2] } : null;
});

function insert(nodeId: string, name: string) {
  const token = `{{${nodeId}.${name}}}`;
  if (props.mode === "ref") {
    emit("update:modelValue", token);
  } else {
    const t = el.value;
    const v = props.modelValue || "";
    let pos = t?.selectionStart ?? v.length;
    let before = v.slice(0, pos);
    // 键入 {{ 触发时，替换掉已输入的 {{
    if (before.endsWith("{{")) before = before.slice(0, -2);
    else if (/\{\{[\w.-]*$/.test(before)) before = before.replace(/\{\{[\w.-]*$/, "");
    const next = before + token + v.slice(pos);
    emit("update:modelValue", next);
    pos = before.length + token.length;
    nextTick(() => { t?.focus(); t?.setSelectionRange(pos, pos); });
  }
  show.value = false;
  filter.value = "";
}

function onInput(ev: Event) {
  const v = (ev.target as HTMLTextAreaElement).value;
  emit("update:modelValue", v);
  const pos = (ev.target as HTMLTextAreaElement).selectionStart ?? v.length;
  const m = v.slice(0, pos).match(/\{\{([\w.-]*)$/);
  if (m) { filter.value = m[1]; show.value = true; } else if (show.value && !m) show.value = false;
}
</script>

<template>
  <div class="vi" :class="{ ref: mode === 'ref' }">
    <n-popover v-model:show="show" trigger="manual" placement="bottom-start" :show-arrow="false"
               raw @clickoutside="show = false">
      <template #trigger>
        <div class="box">
          <template v-if="mode === 'ref'">
            <button class="refbtn" @click="show = !show">
              <span v-if="refDisplay" class="chip"><b>{{ refDisplay.node }}</b>.{{ refDisplay.v }}</span>
              <span v-else-if="modelValue" class="raw">{{ modelValue }}</span>
              <span v-else class="ph">{{ placeholder || "选择变量" }}</span>
            </button>
          </template>
          <template v-else>
            <textarea v-if="rows > 1" ref="el" :value="modelValue" :rows="rows" :placeholder="placeholder"
                      :class="{ mono }" @input="onInput" @keydown.esc="show = false" />
            <input v-else ref="el" :value="modelValue" :placeholder="placeholder" :class="{ mono }"
                   @input="onInput" @keydown.esc="show = false" />
            <button class="xbtn" title="插入变量（或键入 {{ ）" @click="show = !show">{x}</button>
          </template>
        </div>
      </template>
      <div class="pop">
        <input v-model="filter" class="search" placeholder="搜索变量" />
        <div class="scroll">
          <div v-for="g in groups" :key="g.nodeId" class="grp">
            <div class="gt"><span class="gi" :style="{ background: g.color }" />{{ g.title }}</div>
            <button v-for="v in g.vars" :key="v.name" class="var" @click="insert(g.nodeId, v.name)">
              <span class="vn">{{ v.name }}</span>
              <span v-if="v.desc" class="vd">{{ v.desc }}</span>
              <span class="vt">{{ v.type }}</span>
            </button>
          </div>
          <div v-if="!groups.length" class="none">没有可用的上游变量——先把上游节点连到这里</div>
        </div>
      </div>
    </n-popover>
  </div>
</template>

<style scoped>
.vi { width: 100%; }
.box { position: relative; }
textarea, input:not(.search) {
  width: 100%; box-sizing: border-box; resize: vertical;
  background: rgba(255, 255, 255, 0.035); border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 10px; color: var(--text); font-family: inherit; font-size: 12.5px; line-height: 1.6;
  padding: 7px 40px 7px 10px; outline: 0; transition: border-color 0.2s;
}
textarea:focus, input:not(.search):focus { border-color: rgba(143, 124, 255, 0.6); }
.mono { font-family: var(--mono); font-size: 12px; }
.xbtn {
  position: absolute; right: 6px; top: 6px; border: 1px solid var(--line-2); background: rgba(143,124,255,0.1);
  color: var(--accent); font: 600 10.5px var(--mono); border-radius: 6px; padding: 2px 6px; cursor: pointer;
}
.xbtn:hover { background: rgba(143,124,255,0.22); }
.refbtn {
  width: 100%; text-align: left; min-height: 32px; box-sizing: border-box; cursor: pointer;
  background: rgba(255, 255, 255, 0.035); border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 10px; padding: 4px 8px; color: var(--text);
}
.refbtn:hover { border-color: rgba(143, 124, 255, 0.5); }
.chip { display: inline-block; font: 11.5px var(--mono); color: #d6d0ff; background: rgba(143,124,255,0.16); border-radius: 6px; padding: 2px 7px; }
.chip b { font-weight: 600; color: #fff; }
.raw { font: 11.5px var(--mono); color: var(--text-2); }
.ph { font-size: 12px; color: var(--text-3); }

.pop {
  width: 300px; background: rgba(18, 20, 34, 0.98); border: 1px solid var(--line-2);
  border-radius: 14px; box-shadow: var(--shadow); padding: 8px; backdrop-filter: var(--blur);
}
.search {
  width: 100%; box-sizing: border-box; border: 1px solid var(--line); background: rgba(255,255,255,0.04);
  border-radius: 8px; padding: 6px 9px; color: var(--text); font-size: 12px; outline: 0; margin-bottom: 6px;
}
.scroll { max-height: 300px; overflow-y: auto; }
.gt { display: flex; align-items: center; gap: 7px; font-size: 11px; color: var(--text-3); padding: 7px 6px 3px; }
.gi { width: 7px; height: 7px; border-radius: 2px; }
.var {
  width: 100%; display: flex; align-items: center; gap: 8px; border: 0; background: none; cursor: pointer;
  padding: 6px 8px; border-radius: 8px; color: var(--text); text-align: left;
}
.var:hover { background: rgba(143, 124, 255, 0.14); }
.vn { font: 12px var(--mono); }
.vd { flex: 1; font-size: 11px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vt { font: 10px var(--mono); color: var(--accent-2); margin-left: auto; }
.none { padding: 16px 8px; color: var(--text-3); font-size: 12px; text-align: center; }
</style>
