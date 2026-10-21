<script setup lang="ts">
/** 左侧浮动节点库：按类别分组，拖到画布即创建；点击则加在视口中央（触摸设备改为点选→点画布放置）。 */
import { useVueFlow } from "@vue-flow/core";
import { computed, ref } from "vue";
import { useWorkflowStore } from "../../stores/workflow";
import { CATEGORIES, NODE_DEFS } from "./nodeDefs";

const store = useWorkflowStore();
const { screenToFlowCoordinate } = useVueFlow("wf-editor");
const q = ref("");
const collapsed = ref(window.matchMedia("(max-width: 768px)").matches);
/** 触摸设备没有 HTML5 拖拽，点击进入放置模式，下一次点击画布时落地 */
const coarse = ref(window.matchMedia("(pointer: coarse)").matches);

const groups = computed(() => CATEGORIES.map((cat) => ({
  cat,
  items: Object.values(NODE_DEFS).filter((d) => d.category === cat && !d.hidden && d.type !== "start"
    && (!q.value || d.label.includes(q.value) || d.desc.includes(q.value) || d.type.includes(q.value.toLowerCase()))),
})).filter((g) => g.items.length));

function onDragStart(ev: DragEvent, type: string) {
  ev.dataTransfer?.setData("application/x-wf-node", type);
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = "copy";
}
function pickItem(type: string) {
  if (!coarse.value) { addCenter(type); return; }
  store.placing = store.placing === type ? "" : type;
  if (store.placing) collapsed.value = false;
}
const placingLabel = computed(() => store.placing ? NODE_DEFS[store.placing]?.label || store.placing : "");
function addCenter(type: string) {
  const el = document.querySelector(".canvas") as HTMLElement | null;
  const r = el?.getBoundingClientRect();
  const pos = screenToFlowCoordinate({ x: (r?.left ?? 0) + (r?.width ?? 800) / 2, y: (r?.top ?? 0) + (r?.height ?? 600) / 2 });
  store.addNode(type, { x: pos.x - 120 + Math.random() * 40, y: pos.y - 30 + Math.random() * 40 });
}
</script>

<template>
  <div class="pal glass" :class="{ collapsed }">
    <div class="ph">
      <span v-if="!collapsed">节点</span>
      <button class="tg" :aria-label="collapsed ? '展开节点库' : '收起节点库'" @click="collapsed = !collapsed">
        <svg viewBox="0 0 24 24"><path :d="collapsed ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'" /></svg>
      </button>
    </div>
    <template v-if="!collapsed">
      <input v-model="q" class="search" placeholder="搜索节点" aria-label="搜索节点" />
      <div class="scroll">
        <div v-for="g in groups" :key="g.cat" class="grp">
          <div class="gt">{{ g.cat }}</div>
          <div v-for="d in g.items" :key="d.type" class="it" draggable="true" :title="d.desc"
               :class="{ placing: store.placing === d.type }"
               :style="{ '--c': d.color }" role="button" tabindex="0"
               @dragstart="onDragStart($event, d.type)" @click="pickItem(d.type)" @keydown.enter="pickItem(d.type)">
            <span class="ic"><svg viewBox="0 0 24 24"><path :d="d.icon" /></svg></span>
            <span class="tx"><span class="nm">{{ d.label }}</span><span class="ds">{{ d.desc }}</span></span>
          </div>
        </div>
      </div>
      <div v-if="store.placing" class="placing-bar">
        已选「{{ placingLabel }}」· 点击画布放置
        <button aria-label="取消放置" @click="store.placing = ''">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pal {
  position: absolute; top: 12px; left: 12px; bottom: 76px; width: 232px; z-index: 6;
  border-radius: 18px; background: rgba(12, 14, 24, 0.86); display: flex; flex-direction: column;
  box-shadow: var(--shadow); transition: width 0.25s var(--ease);
}
.pal.collapsed { width: 46px; bottom: auto; }
.ph { display: flex; align-items: center; justify-content: space-between; padding: 12px 10px 6px 16px; font-size: 13px; font-weight: 600; }
.pal.collapsed .ph { padding: 8px; justify-content: center; }
.tg { border: 0; background: none; padding: 4px; border-radius: 7px; cursor: pointer; }
.tg svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; fill: none; }
.tg:hover { background: var(--glass-hover); }
.search {
  margin: 4px 12px 8px; border: 1px solid var(--line); background: rgba(255,255,255,0.035);
  border-radius: 9px; padding: 6px 10px; color: var(--text); font-size: 12px; outline: 0;
}
.search:focus { border-color: rgba(143, 124, 255, 0.5); }
.scroll { flex: 1; overflow-y: auto; padding: 0 8px 10px; }
.gt { font-size: 10.5px; color: var(--text-3); letter-spacing: 1px; padding: 10px 8px 5px; font-weight: 600; }
.it {
  display: flex; align-items: center; gap: 10px; padding: 7px 8px; border-radius: 10px;
  cursor: grab; transition: background 0.15s, transform 0.15s var(--ease);
  border: 1px solid transparent;
}
.it:hover { background: color-mix(in srgb, var(--c) 10%, transparent); border-color: color-mix(in srgb, var(--c) 25%, transparent); transform: translateX(2px); }
.it:active { cursor: grabbing; transform: scale(0.98); }
.it:focus-visible { outline: 2px solid var(--accent); }
.ic {
  width: 28px; height: 28px; border-radius: 8px; flex-shrink: 0; display: grid; place-items: center;
  background: color-mix(in srgb, var(--c) 18%, transparent);
}
.ic svg { width: 14px; height: 14px; stroke: var(--c); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.tx { display: flex; flex-direction: column; min-width: 0; }
.nm { font-size: 12.5px; color: var(--text); }
.ds { font-size: 10.5px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 放置模式：条目高亮 + 底部提示条 */
.it.placing {
  background: color-mix(in srgb, var(--c) 22%, transparent);
  border-color: color-mix(in srgb, var(--c) 55%, transparent);
}
.placing-bar {
  display: flex; align-items: center; gap: 8px; margin: 6px 10px 10px; padding: 7px 9px;
  border-radius: 9px; font-size: 11px; color: var(--accent-2);
  background: rgba(45, 212, 191, 0.1); border: 1px solid rgba(45, 212, 191, 0.3);
}
.placing-bar button { margin-left: auto; border: 0; background: none; padding: 3px; border-radius: 5px; cursor: pointer; display: grid; place-items: center; }
.placing-bar button svg { width: 12px; height: 12px; stroke: var(--text-3); stroke-width: 2; }
.placing-bar button:hover { background: rgba(255, 255, 255, 0.08); }
</style>
