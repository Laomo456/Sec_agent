<script setup lang="ts">
/** 画布上的通用节点：图标/标题/摘要/多出口/运行状态。容器节点（迭代/循环）是可放子节点的框。 */
import { Handle, Position } from "@vue-flow/core";
import { NodeResizer } from "@vue-flow/node-resizer";
import { computed } from "vue";
import { useWorkflowStore } from "../../stores/workflow";
import { NODE_DEFS, handlesOf } from "./nodeDefs";

const props = defineProps<{ id: string; type: string; data: Record<string, any>; selected?: boolean }>();
const store = useWorkflowStore();
const def = computed(() => NODE_DEFS[props.type]);
const run = computed(() => store.nodeRuns[props.id]);
const handles = computed(() => handlesOf({ type: props.type, data: props.data }));
const multi = computed(() => handles.value.length > 1 || handles.value.some((h) => h.label));
const summary = computed(() => {
  const s = def.value?.summary?.(props.data) ?? "";
  return s.length > 90 ? `${s.slice(0, 90)}…` : s;
});
const dur = computed(() => {
  const ms = run.value?.duration_ms;
  return ms == null ? "" : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
});
const statusIcon: Record<string, string> = {
  succeeded: "m5 12.5 4.5 4.5L19 7.5", failed: "M6 6l12 12M18 6 6 18",
  exception: "M12 7v6M12 16.5v.5", skipped: "M5 12h14",
  stopped: "M7 7h10v10H7z", stopping: "M7 7h10v10H7z",
  manual: "M5 12h14m-5-5 5 5-5 5", cached: "m5 12.5 4.5 4.5L19 7.5",
};
</script>

<template>
  <!-- 注释便签 -->
  <div v-if="type === 'note'" class="note" :class="{ selected }">
    <div class="note-txt">{{ data.text }}</div>
  </div>

  <!-- 容器起点：小圆点 -->
  <div v-else-if="def?.hidden" class="mini" :style="{ '--c': def.color }">
    <svg viewBox="0 0 24 24"><path :d="def.icon" /></svg>
    <Handle type="source" :position="Position.Right" id="out" class="h" />
  </div>

  <!-- 容器：迭代 / 循环 -->
  <div v-else-if="def?.container" class="container" :class="[run?.status, { selected }]"
       :style="{ '--c': def.color }">
    <NodeResizer v-if="selected" :min-width="360" :min-height="200" color="var(--accent)" />
    <Handle type="target" :position="Position.Left" class="h in" />
    <div class="c-head">
      <span class="ic"><svg viewBox="0 0 24 24"><path :d="def.icon" /></svg></span>
      <span class="title">{{ data.title }}</span>
      <span class="tag">{{ type === "iteration" ? (data.parallel ? "并行迭代" : "迭代") : `最多 ${data.max_loops || 5} 轮` }}</span>
      <span v-if="run?.iterations" class="tag live">已执行 {{ run.iterations }} 次</span>
      <span v-if="dur" class="dur">{{ dur }}</span>
    </div>
    <div class="c-body"><span class="hint">把节点拖进框内，组成每一轮要执行的子流程</span></div>
    <Handle type="source" :position="Position.Right" id="out" class="h out" />
  </div>

  <!-- 普通节点 -->
  <div v-else-if="def" class="node" :class="[run?.status, { selected }]" :style="{ '--c': def.color }">
    <div class="ring" />
    <Handle v-if="!def.noInput" type="target" :position="Position.Left" class="h in" />
    <div class="head">
      <span class="ic"><svg viewBox="0 0 24 24"><path :d="def.icon" /></svg></span>
      <span class="title">{{ data.title || def.label }}</span>
      <span v-if="run" class="st" :class="run.status">
        <i v-if="run.status === 'running'" class="spin" />
        <svg v-else-if="statusIcon[run.status]" viewBox="0 0 24 24"><path :d="statusIcon[run.status]" /></svg>
      </span>
    </div>
    <div v-if="summary" class="sum">{{ summary }}</div>
    <div v-if="run?.stream && run.status === 'running'" class="stream">{{ run.stream.slice(-160) }}</div>
    <div v-if="run?.error" class="err">{{ run.error.slice(0, 120) }}</div>

    <!-- 多出口：每个出口一行，handle 贴在行右侧 -->
    <div v-if="multi" class="outs">
      <div v-for="h in handles" :key="h.id" class="orow" :class="h.tone">
        <span class="olab">{{ h.label || "下一步" }}</span>
        <Handle type="source" :position="Position.Right" :id="h.id" class="h out rel" />
      </div>
    </div>
    <Handle v-else-if="handles.length" type="source" :position="Position.Right" :id="handles[0].id" class="h out" />

    <div v-if="dur" class="foot">{{ dur }}</div>
  </div>
</template>

<style scoped>
.node {
  position: relative;
  width: 244px;
  border-radius: 16px;
  background: rgba(18, 20, 34, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.09);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.4);
  padding: 11px 13px 10px;
  transition: border-color 0.2s, box-shadow 0.25s, transform 0.15s;
  backdrop-filter: blur(12px);
}
.node::before {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; border-radius: 16px 16px 0 0;
  background: linear-gradient(90deg, var(--c), transparent);
  opacity: 0.9;
}
.node:hover { border-color: rgba(255, 255, 255, 0.18); }
.node.selected {
  border-color: var(--c);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--c) 22%, transparent), 0 10px 34px rgba(0, 0, 0, 0.5);
}
.ring { position: absolute; inset: -1px; border-radius: 17px; pointer-events: none; }
.node.running .ring {
  padding: 1.5px;
  background: conic-gradient(from var(--a, 0deg), transparent 0 60%, var(--c), #fff 80%, transparent 90%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  animation: ring 1.6s linear infinite;
}
@property --a { syntax: "<angle>"; inherits: false; initial-value: 0deg; }
@keyframes ring { to { --a: 360deg; } }
.node.succeeded { border-color: rgba(52, 211, 153, 0.55); }
.node.failed { border-color: rgba(248, 113, 113, 0.7); box-shadow: 0 0 24px rgba(248, 113, 113, 0.15); }
.node.exception { border-color: rgba(251, 191, 36, 0.6); }
.node.skipped { opacity: 0.45; }

.head { display: flex; align-items: center; gap: 9px; }
.ic {
  width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0; display: grid; place-items: center;
  background: color-mix(in srgb, var(--c) 20%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 35%, transparent);
}
.ic svg { width: 14px; height: 14px; stroke: var(--c); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.title { flex: 1; min-width: 0; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.st { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; flex-shrink: 0; }
.st svg { width: 11px; height: 11px; stroke: #fff; stroke-width: 3; fill: none; stroke-linecap: round; }
.st.succeeded { background: var(--ok); }
.st.failed { background: var(--err); }
.st.exception { background: var(--warn); }
.st.skipped { background: var(--text-3); }
.st.stopped, .st.stopping, .st.manual { background: var(--warn); }
.st.cached { background: var(--accent-2); }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.15); border-top-color: var(--c); animation: spin 0.8s linear infinite; }

.sum {
  margin-top: 8px; font-size: 11.5px; color: var(--text-3); line-height: 1.5;
  background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 5px 8px;
  font-family: var(--mono); word-break: break-all;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.stream {
  margin-top: 6px; font-size: 11px; color: var(--text-2); line-height: 1.5; max-height: 52px; overflow: hidden;
  mask-image: linear-gradient(transparent, #000 35%);
}
.err { margin-top: 6px; font-size: 11px; color: #fecaca; background: rgba(248, 113, 113, 0.1); border-radius: 7px; padding: 4px 8px; }

.outs { margin: 8px -13px 0 0; display: flex; flex-direction: column; gap: 3px; }
.orow {
  position: relative; display: flex; justify-content: flex-end; align-items: center;
  padding: 3px 14px 3px 0; font-size: 10.5px; font-family: var(--mono); color: var(--text-2);
}
.orow.muted { color: var(--text-3); }
.orow.err { color: var(--err); }
.olab { background: rgba(255,255,255,0.04); border-radius: 5px; padding: 1px 7px; }
.foot { position: absolute; right: 10px; bottom: -18px; font-size: 10px; font-family: var(--mono); color: var(--text-3); }

/* handles */
.h {
  width: 11px !important; height: 11px !important; border-radius: 50% !important;
  background: #0b0d16 !important; border: 2px solid var(--c, var(--accent)) !important;
  transition: transform 0.15s, box-shadow 0.15s;
}
.h:hover { transform: scale(1.35); box-shadow: 0 0 0 4px color-mix(in srgb, var(--c) 25%, transparent); }
.h.rel { position: absolute !important; right: -6px !important; top: 50% !important; transform: translateY(-50%); }
.h.rel:hover { transform: translateY(-50%) scale(1.35); }

/* 容器 */
.container {
  width: 100%; height: 100%; box-sizing: border-box; position: relative;
  border-radius: 18px; border: 1.5px dashed color-mix(in srgb, var(--c) 55%, transparent);
  background: color-mix(in srgb, var(--c) 5%, rgba(10, 11, 20, 0.5));
}
.container.selected { border-style: solid; border-color: var(--c); }
.container.running { animation: glow 1.8s ease-in-out infinite; }
.container.succeeded { border-color: rgba(52, 211, 153, 0.6); }
.container.failed { border-color: rgba(248, 113, 113, 0.7); }
@keyframes glow { 50% { box-shadow: 0 0 30px color-mix(in srgb, var(--c) 30%, transparent); } }
.c-head { display: flex; align-items: center; gap: 9px; padding: 10px 14px; }
.tag { font-size: 10.5px; color: var(--c); border: 1px solid color-mix(in srgb, var(--c) 40%, transparent); border-radius: 999px; padding: 1px 8px; }
.tag.live { color: var(--ok); border-color: rgba(52, 211, 153, 0.4); }
.dur { margin-left: auto; font-size: 10.5px; font-family: var(--mono); color: var(--text-3); }
.c-body { position: absolute; inset: 44px 0 0 0; display: grid; place-items: end center; padding-bottom: 12px; pointer-events: none; }
.hint { font-size: 11px; color: var(--text-3); opacity: 0.7; }

.mini {
  width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center;
  background: rgba(18, 20, 34, 0.95); border: 1.5px solid var(--c);
}
.mini svg { width: 13px; height: 13px; fill: var(--c); }

.note {
  width: 220px; min-height: 80px; padding: 12px 14px; border-radius: 12px;
  background: rgba(253, 230, 138, 0.1); border: 1px solid rgba(253, 230, 138, 0.3);
  color: #fef3c7; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap;
}
.note.selected { border-color: #fde68a; }
</style>
