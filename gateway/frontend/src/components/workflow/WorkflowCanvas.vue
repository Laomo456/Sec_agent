<script setup lang="ts">
/** Vue Flow 画布：拖拽建节点、拖入容器、连线校验、右键菜单、快捷键、自动布局。 */
import { VueFlow, useVueFlow, MarkerType, getRectOfNodes, getTransformForBounds, type Connection, type NodeDragEvent } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { MiniMap } from "@vue-flow/minimap";
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useWorkflowStore } from "../../stores/workflow";
import { NODE_DEFS, handlesOf } from "./nodeDefs";
import WfNodeCard from "./WfNodeCard.vue";
import NodePalette from "./NodePalette.vue";

const store = useWorkflowStore();
const flow = useVueFlow("wf-editor");
const { screenToFlowCoordinate, zoomIn, zoomOut, getIntersectingNodes, findNode, setViewport } = flow;

/** 适应画布，但给左侧浮动节点库留出位置（否则最左边的节点被盖住） */
async function fitView(opts: { padding?: number; duration?: number } = {}) {
  const visible = flow.getNodes.value;
  if (!wrap.value || !visible.length || visible.some((n) => !n.dimensions.width)) return;
  const pal = document.querySelector(".pal") as HTMLElement | null;
  const reserve = pal ? pal.offsetWidth + 24 : 0;
  const bounds = getRectOfNodes(visible);
  const v = getTransformForBounds(bounds, Math.max(100, wrap.value.clientWidth - reserve),
                                  wrap.value.clientHeight - 80, 0.2, 1.1, opts.padding ?? 0.2);
  await setViewport({ x: v.x + reserve, y: v.y + 16, zoom: v.zoom }, { duration: opts.duration });
}
const wrap = ref<HTMLElement>();
const zoom = ref(1);
let pendingFit = true;
function onNodesInitialized() {
  if (!pendingFit) return;
  pendingFit = false;
  nextTick(() => fitView({ padding: 0.25 }));
}

const nodeTypes = Object.fromEntries(Object.keys(NODE_DEFS).map((t) => [t, markRaw(WfNodeCard)]));

/** store 是唯一真相：画布节点/连线由 store 派生，改动经事件写回 store */
const nodes = computed(() => (store.wf?.nodes || []).map((n) => ({
  ...n,
  selected: n.id === store.selectedId,
  zIndex: NODE_DEFS[n.type]?.container ? 0 : n.parentNode ? 2 : 1,
  deletable: !(n.type === "start" && !n.parentNode) && !n.type.endsWith("_start"),
})));
const edges = computed(() => (store.wf?.edges || []).map((e) => {
  const src = store.nodeRuns[e.source];
  const tgt = store.nodeRuns[e.target];
  const active = ["succeeded", "cached", "manual"].includes(src?.status || "") && !!tgt && tgt.status !== "skipped";
  const flowing = store.running && !store.stopping && active && tgt.status === "running";
  const handle = e.sourceHandle || "out";
  const tone = handle === "fail" ? "fail" : handle === "else" ? "else" : "";
  return {
    ...e, type: "smoothstep", animated: flowing, pathOptions: { borderRadius: 14, offset: 18 },
    class: [active ? "active" : "", flowing ? "flowing" : "", tone, tgt?.status === "skipped" ? "skipped" : ""].join(" "),
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: active ? "#34d399" : tone === "fail" ? "#f87171" : "#5b6078" },
  };
}));

function onNodesChange(changes: any[]) {
  if (!store.wf) return;
  for (const c of changes) {
    const n = store.wf.nodes.find((x) => x.id === c.id);
    if (!n) continue;
    if (c.type === "position" && c.position) n.position = { ...c.position };
    if (c.type === "dimensions" && c.dimensions && NODE_DEFS[n.type]?.container && c.resizing !== undefined) {
      n.style = { ...(n.style || {}), width: `${c.dimensions.width}px`, height: `${c.dimensions.height}px` };
    }
    if (c.type === "select" && c.selected) store.selectedId = c.id;
    if (c.type === "remove") store.removeNodes([c.id]);
  }
}
function onEdgesChange(changes: any[]) {
  const rm = changes.filter((c) => c.type === "remove").map((c) => c.id);
  if (rm.length) store.removeEdges(rm);
}

/** 连线规则：不连自己、不跨容器、不连到开始节点、不造环 */
function isValidConnection(c: Connection): boolean {
  if (!store.wf || c.source === c.target) return false;
  const s = store.wf.nodes.find((n) => n.id === c.source);
  const t = store.wf.nodes.find((n) => n.id === c.target);
  if (!s || !t || NODE_DEFS[t.type]?.noInput) return false;
  if ((s.parentNode || "") !== (t.parentNode || "")) return false;
  const adj = new Map<string, string[]>();
  for (const e of store.wf.edges) adj.set(e.source, [...(adj.get(e.source) || []), e.target]);
  const stack = [c.target];
  const seen = new Set<string>();
  while (stack.length) {
    const cur = stack.pop()!;
    if (cur === c.source) return false;
    if (seen.has(cur)) continue;
    seen.add(cur);
    stack.push(...(adj.get(cur) || []));
  }
  return true;
}
function onConnect(c: Connection) { store.addEdge(c); }

/* ── 拖拽：从节点库拖入 / 拖入容器 ── */
function onDragOver(ev: DragEvent) {
  if (ev.dataTransfer?.types.includes("application/x-wf-node")) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  }
}
function containerAt(pos: { x: number; y: number }): string | undefined {
  const hit = (store.wf?.nodes || []).filter((n) => NODE_DEFS[n.type]?.container).find((n) => {
    const g = findNode(n.id);
    if (!g) return false;
    const { x, y } = g.computedPosition;
    return pos.x > x && pos.y > y && pos.x < x + g.dimensions.width && pos.y < y + g.dimensions.height;
  });
  return hit?.id;
}
function onDrop(ev: DragEvent) {
  const type = ev.dataTransfer?.getData("application/x-wf-node");
  if (!type) return;
  const pos = screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY });
  addAt(type, pos);
}
function addAt(type: string, pos: { x: number; y: number }, link?: { source: string; handle: string; target?: string; edgeId?: string }) {
  const def = NODE_DEFS[type];
  const parent = def?.container ? undefined : containerAt(pos);
  let rel = { x: pos.x - 120, y: pos.y - 30 };
  if (parent) {
    const g = findNode(parent)!;
    rel = { x: pos.x - g.computedPosition.x - 120, y: pos.y - g.computedPosition.y - 30 };
  }
  const node = store.addNode(type, rel, parent);
  if (node && link) {
    if (link.edgeId) store.removeEdges([link.edgeId]);
    store.addEdge({ source: link.source, target: node.id, sourceHandle: link.handle });
    const hs = handlesOf(node);
    if (link.target && hs.length) store.addEdge({ source: node.id, target: link.target, sourceHandle: hs[0].id });
  }
}

/** 拖动普通节点进出容器：落点在容器内就挂进去（坐标换算为相对坐标），拖出来就解绑 */
function onNodeDragStop(e: NodeDragEvent) {
  const n = store.wf?.nodes.find((x) => x.id === e.node.id);
  if (!n || NODE_DEFS[n.type]?.container || n.type.endsWith("_start") || n.type === "start") return;
  const g = findNode(n.id)!;
  const center = { x: g.computedPosition.x + g.dimensions.width / 2, y: g.computedPosition.y + g.dimensions.height / 2 };
  const target = containerAt(center);
  if ((target || undefined) === (n.parentNode || undefined)) return;
  store.checkpoint();
  // 换容器：挂在它身上的连线跨越了边界，一律删掉
  store.wf!.edges = store.wf!.edges.filter((ed) => ed.source !== n.id && ed.target !== n.id);
  if (target) {
    const p = findNode(target)!;
    n.position = { x: g.computedPosition.x - p.computedPosition.x, y: g.computedPosition.y - p.computedPosition.y };
    n.parentNode = target;
    n.extent = "parent";
  } else {
    n.position = { ...g.computedPosition };
    delete n.parentNode;
    delete n.extent;
  }
  void getIntersectingNodes;
}
function onNodeDragStart() { store.checkpoint(); }

/* ── 右键菜单 / 连线 + 号插入 ── */
const menu = ref<{ x: number; y: number; flow: { x: number; y: number }; nodeId?: string; link?: any } | null>(null);
function onPaneContext(ev: MouseEvent) {
  ev.preventDefault();
  openMenu(ev.clientX, ev.clientY);
}
function openMenu(cx: number, cy: number, extra: Partial<NonNullable<typeof menu.value>> = {}) {
  const r = wrap.value!.getBoundingClientRect();
  menu.value = { x: cx - r.left, y: cy - r.top, flow: screenToFlowCoordinate({ x: cx, y: cy }), ...extra };
}
function onNodeContext({ event, node }: { event: MouseEvent | TouchEvent; node: any }) {
  event.preventDefault();
  const ev = event as MouseEvent;
  store.selectedId = node.id;
  openMenu(ev.clientX, ev.clientY, { nodeId: node.id });
}
function onEdgeClick({ event, edge }: any) {
  const ev = event as MouseEvent;
  openMenu(ev.clientX, ev.clientY, { link: { source: edge.source, handle: edge.sourceHandle || "out", target: edge.target, edgeId: edge.id } });
}
/** 从出口拖线到空白处松手 → 弹出菜单，选中的节点自动接上 */
const pendingConn = ref<{ source: string; handle: string } | null>(null);
function onConnectStart({ nodeId, handleId, handleType }: any) {
  pendingConn.value = handleType === "source" ? { source: nodeId, handle: handleId || "out" } : null;
}
function onConnectEnd(ev?: MouseEvent | TouchEvent) {
  const pc = pendingConn.value;
  pendingConn.value = null;
  if (!pc || !ev) return;
  const t = (ev as MouseEvent).target as HTMLElement;
  if (t?.closest?.(".vue-flow__handle")) return;
  const me = ev as MouseEvent;
  openMenu(me.clientX, me.clientY, { link: { source: pc.source, handle: pc.handle } });
}
function pick(type: string) {
  const m = menu.value!;
  addAt(type, m.flow, m.link);
  menu.value = null;
}

/** 触摸端放置模式：点空白处落地；否则照旧取消选中/关菜单 */
function onPaneClick(ev: MouseEvent) {
  if (store.placing) {
    addAt(store.placing, screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY }));
    store.placing = "";
    return;
  }
  store.selectedId = "";
  menu.value = null;
}
const addable = computed(() => Object.values(NODE_DEFS).filter((d) => !d.hidden && d.type !== "start"
  && !(menu.value?.link && d.noInput)));

/* ── 自动布局：按最长路径分层，从左到右排列（容器内部各自布局） ── */
function autoLayout() {
  if (!store.wf) return;
  store.checkpoint();
  const groups = new Map<string, typeof store.wf.nodes>();
  for (const n of store.wf.nodes) {
    if (n.type === "note") continue;
    const k = n.parentNode || "";
    groups.set(k, [...(groups.get(k) || []), n]);
  }
  for (const [parent, members] of groups) {
    const ids = new Set(members.map((n) => n.id));
    const es = store.wf.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    const layer = new Map<string, number>(members.map((n) => [n.id, 0]));
    for (let pass = 0; pass < members.length; pass++) {
      let changed = false;
      for (const e of es) {
        const v = layer.get(e.source)! + 1;
        if (v > layer.get(e.target)!) { layer.set(e.target, v); changed = true; }
      }
      if (!changed) break;
    }
    const cols = new Map<number, string[]>();
    for (const [id, l] of layer) cols.set(l, [...(cols.get(l) || []), id]);
    const gx = parent ? 250 : 320, gy = parent ? 110 : 150, ox = parent ? 30 : 60, oy = parent ? 60 : 80;
    for (const [l, colIds] of cols) {
      colIds.forEach((id, i) => {
        const n = members.find((x) => x.id === id)!;
        n.position = { x: ox + l * gx, y: oy + i * gy };
      });
    }
    if (parent) {   // 容器尺寸跟着内容走
      const p = store.wf.nodes.find((n) => n.id === parent)!;
      const w = Math.max(360, ox + cols.size * gx + 40);
      const h = Math.max(200, oy + Math.max(...[...cols.values()].map((c) => c.length)) * gy + 20);
      p.style = { ...(p.style || {}), width: `${w}px`, height: `${h}px` };
    }
  }
  nextTick(() => fitView({ padding: 0.2, duration: 500 }));
}

/* ── 快捷键 ── */
function onKey(ev: KeyboardEvent) {
  const t = ev.target as HTMLElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  const mod = ev.ctrlKey || ev.metaKey;
  if (mod && ev.key.toLowerCase() === "z") { ev.preventDefault(); ev.shiftKey ? store.redo() : store.undo(); }
  else if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); store.redo(); }
  else if (mod && ev.key.toLowerCase() === "s") { ev.preventDefault(); store.save(); }
  else if (mod && ev.key.toLowerCase() === "d" && store.selectedId) { ev.preventDefault(); store.duplicateNode(store.selectedId); }
  else if ((ev.key === "Delete" || ev.key === "Backspace") && store.selectedId) { ev.preventDefault(); store.removeNodes([store.selectedId]); }
  else if (ev.key === "Escape") { store.selectedId = ""; menu.value = null; store.placing = ""; }
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
watch(() => store.wf?.id, () => { pendingFit = true; }, { immediate: true });
</script>
<template>
  <div ref="wrap" class="canvas" :class="{ placing: !!store.placing }" @dragover="onDragOver" @drop="onDrop" @click="menu = null">
    <VueFlow id="wf-editor" :nodes="nodes" :edges="edges" :node-types="nodeTypes"
             :apply-default="false" :is-valid-connection="isValidConnection"
             :min-zoom="0.2" :max-zoom="2" :snap-to-grid="true" :snap-grid="[8, 8]"
             :delete-key-code="null" :connection-radius="28" elevate-edges-on-select
             :default-edge-options="{ type: 'smoothstep' }"
             @nodes-change="onNodesChange" @edges-change="onEdgesChange" @connect="onConnect"
             @nodes-initialized="onNodesInitialized"
             @connect-start="onConnectStart" @connect-end="onConnectEnd"
             @node-drag-start="onNodeDragStart" @node-drag-stop="onNodeDragStop"
             @pane-click="onPaneClick" @pane-context-menu="onPaneContext"
             @node-context-menu="onNodeContext" @edge-click="onEdgeClick"
             @node-click="({ node }: any) => store.selectedId = node.id"
             @viewport-change="(v: any) => zoom = v.zoom">
      <Background pattern-color="rgba(255,255,255,0.07)" :gap="22" :size="1.3" />
      <MiniMap pannable zoomable class="mm" :node-color="(n: any) => NODE_DEFS[n.type]?.color || '#666'"
               mask-color="rgba(6,7,12,0.7)" />
    </VueFlow>

    <!-- 左侧浮动节点库 -->
    <NodePalette />

    <!-- 底部工具条 -->
    <div class="tools glass">
      <button title="撤销 (Ctrl+Z)" :disabled="!store.undoStack.length" @click="store.undo()">
        <svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3" /></svg></button>
      <button title="重做 (Ctrl+Shift+Z)" :disabled="!store.redoStack.length" @click="store.redo()">
        <svg viewBox="0 0 24 24"><path d="m15 14 5-5-5-5M20 9H9a5 5 0 0 0 0 10h3" /></svg></button>
      <span class="div" />
      <button title="缩小" @click="zoomOut()"><svg viewBox="0 0 24 24"><path d="M5 12h14" /></svg></button>
      <span class="zm">{{ Math.round(zoom * 100) }}%</span>
      <button title="放大" @click="zoomIn()"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></button>
      <button title="适应画布" @click="fitView({ padding: 0.2, duration: 400 })">
        <svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg></button>
      <span class="div" />
      <button title="自动整理布局" @click="autoLayout">
        <svg viewBox="0 0 24 24"><path d="M4 5h6v5H4zM14 14h6v5h-6zM14 5h6v5h-6zM10 7.5h4M17 10v4" /></svg></button>
    </div>

    <!-- 右键 / 插入菜单 -->
    <Transition name="menu">
      <div v-if="menu" class="menu glass" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }" @click.stop>
        <template v-if="menu.nodeId">
          <button @click="store.duplicateNode(menu.nodeId!); menu = null">复制节点 <kbd>Ctrl+D</kbd></button>
          <button class="danger" @click="store.removeNodes([menu.nodeId!]); menu = null">删除节点 <kbd>Del</kbd></button>
        </template>
        <template v-else>
          <div class="mh">{{ menu.link ? "插入节点" : "添加节点" }}</div>
          <div class="mg">
            <button v-for="d in addable" :key="d.type" @click="pick(d.type)">
              <span class="mi" :style="{ '--c': d.color }"><svg viewBox="0 0 24 24"><path :d="d.icon" /></svg></span>
              {{ d.label }}
            </button>
          </div>
        </template>
      </div>
    </Transition>

    <div v-if="store.placing" class="phint glass">点击画布放置节点 · Esc 取消</div>
    <div v-if="!store.wf?.nodes.length" class="empty">从左侧拖入节点开始搭建</div>
  </div>
</template>

<style scoped>
.canvas { position: relative; width: 100%; height: 100%; }
:deep(.vue-flow) { background: transparent; }
:deep(.vue-flow__edge-path) { stroke: #4b5068; stroke-width: 2; transition: stroke 0.3s; }
:deep(.vue-flow__edge.selected .vue-flow__edge-path),
:deep(.vue-flow__edge:hover .vue-flow__edge-path) { stroke: var(--accent); }
:deep(.vue-flow__edge.active .vue-flow__edge-path) { stroke: #34d399; }
:deep(.vue-flow__edge.flowing .vue-flow__edge-path) { stroke: var(--accent-2); stroke-dasharray: 6 5; }
:deep(.vue-flow__edge.fail .vue-flow__edge-path) { stroke: rgba(248, 113, 113, 0.7); stroke-dasharray: 5 4; }
:deep(.vue-flow__edge.else .vue-flow__edge-path) { stroke-dasharray: 2 4; }
:deep(.vue-flow__edge.skipped .vue-flow__edge-path) { opacity: 0.3; }
:deep(.vue-flow__edge) { cursor: pointer; }
:deep(.vue-flow__connection-path) { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 5 4; }
:deep(.vue-flow__node) { border-radius: 16px; }
:deep(.vue-flow__node.dragging) { cursor: grabbing; }
:deep(.vue-flow__node.dragging > *) { box-shadow: 0 18px 50px rgba(0, 0, 0, 0.6); }
:deep(.vue-flow__handle.connectingfrom), :deep(.vue-flow__handle.valid) { box-shadow: 0 0 0 5px rgba(143, 124, 255, 0.35); }
.mm { border-radius: 12px; overflow: hidden; border: 1px solid var(--line); background: rgba(10, 11, 20, 0.8) !important; }

.tools {
  position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: 5;
  display: flex; align-items: center; gap: 2px; padding: 5px; border-radius: 14px;
  background: rgba(14, 16, 26, 0.85); box-shadow: var(--shadow);
}
.tools button {
  width: 32px; height: 32px; border: 0; background: none; border-radius: 9px; cursor: pointer;
  display: grid; place-items: center;
}
.tools button:hover:not(:disabled) { background: var(--glass-hover); }
.tools button:disabled { opacity: 0.3; cursor: default; }
.tools svg { width: 16px; height: 16px; stroke: var(--text-2); stroke-width: 1.9; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.zm { font: 11px var(--mono); color: var(--text-3); width: 42px; text-align: center; }
.div { width: 1px; height: 18px; background: var(--line-2); margin: 0 4px; }

.menu {
  position: absolute; z-index: 30; min-width: 190px; padding: 6px; border-radius: 14px;
  background: rgba(16, 18, 30, 0.97); box-shadow: var(--shadow);
}
.menu > button, .mg button {
  width: 100%; display: flex; align-items: center; gap: 9px; border: 0; background: none; cursor: pointer;
  color: var(--text); font-size: 12.5px; padding: 7px 9px; border-radius: 8px; text-align: left;
}
.menu button:hover { background: rgba(143, 124, 255, 0.14); }
.menu .danger { color: #fecaca; }
.menu .danger:hover { background: rgba(248, 113, 113, 0.14); }
.menu kbd { margin-left: auto; font: 10px var(--mono); color: var(--text-3); }
.mh { font-size: 11px; color: var(--text-3); padding: 5px 9px; }
.mg { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; width: 300px; }
.mi {
  width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center; flex-shrink: 0;
  background: color-mix(in srgb, var(--c) 20%, transparent);
}
.mi svg { width: 12px; height: 12px; stroke: var(--c); stroke-width: 2.2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.menu-enter-active, .menu-leave-active { transition: opacity 0.15s, transform 0.15s var(--ease); }
.menu-enter-from, .menu-leave-to { opacity: 0; transform: scale(0.96) translateY(-4px); }
.empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-3); pointer-events: none; }
.canvas.placing { cursor: crosshair; }
.canvas.placing :deep(.vue-flow__pane) { cursor: crosshair; }
.phint {
  position: absolute; left: 50%; bottom: 64px; transform: translateX(-50%); z-index: 6;
  padding: 7px 14px; border-radius: 10px; font-size: 12px; color: var(--accent-2);
  pointer-events: none; white-space: nowrap;
}
</style>
