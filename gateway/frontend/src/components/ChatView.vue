<script setup lang="ts">
import { ref } from "vue";
import { useMessage } from "naive-ui";
import Composer from "./Composer.vue";
import MessageStream from "./MessageStream.vue";
import OutputDrawer from "./OutputDrawer.vue";
import SessionSidebar from "./SessionSidebar.vue";
import { useSessionStore } from "../stores/session";

const store = useSessionStore();
const msg = useMessage();
const menuOpen = ref(false);
/** 桌面端侧栏收起状态，记在 localStorage；移动端仍走抽屉（menuOpen） */
const collapsed = ref(localStorage.getItem("agg.sidebar") === "collapsed");
function toggleSide() {
  collapsed.value = !collapsed.value;
  localStorage.setItem("agg.sidebar", collapsed.value ? "collapsed" : "open");
}
const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/* 拖拽上传：只接管"文件"拖拽（输入框内拖选文本等原生行为不受影响） */
const dragging = ref(false);
let dragDepth = 0;   // enter/leave 深度计数：子元素边界切换不会闪掉高亮
const hasFiles = (ev: DragEvent) => !!ev.dataTransfer?.types?.includes("Files");
function onDragEnter(ev: DragEvent) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragDepth++;
  dragging.value = true;
}
function onDragOver(ev: DragEvent) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();   // 必须：不 preventDefault 的目标不会触发 drop
}
function onDragLeave() {
  if (!dragDepth) return;
  if (--dragDepth <= 0) { dragDepth = 0; dragging.value = false; }
}
async function onDrop(ev: DragEvent) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragDepth = 0;
  dragging.value = false;
  try {
    await store.uploadFiles(ev.dataTransfer?.files ?? []);
  } catch (e: any) {
    msg.error(`上传失败：${e.message}`);
  }
}
</script>

<template>
  <div class="chat">
    <SessionSidebar :open="menuOpen" :collapsed="collapsed" @close="menuOpen = false" />
    <section class="main" @dragenter="onDragEnter" @dragover="onDragOver"
             @dragleave="onDragLeave" @drop="onDrop">
      <header class="top">
        <button class="burger" aria-label="会话列表" @click="menuOpen = true">
          <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10" /></svg>
        </button>
        <button class="fold" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"
                :aria-expanded="!collapsed" :title="collapsed ? '展开侧栏' : '收起侧栏'"
                @click="toggleSide">
          <svg viewBox="0 0 24 24">
            <rect x="3.5" y="4.5" width="17" height="15" rx="3" />
            <path d="M9.5 4.5v15" />
            <path :d="collapsed ? 'm13.5 10 2 2-2 2' : 'm15.5 10-2 2 2 2'" />
          </svg>
        </button>
        <div class="title">
          <div class="t">{{ store.currentTitle }}</div>
          <div class="sid">{{ store.currentId }}</div>
        </div>
        <div class="pills">
          <span v-if="store.cancelling" class="pill warn"><span class="dot" />取消中</span>
          <span v-else-if="store.running" class="pill run"><span class="dot" />执行中</span>
          <span v-else class="pill" :class="store.connected ? 'ok' : 'err'">
            <span class="dot" />{{ store.connected ? "就绪" : "离线" }}
          </span>
          <span class="pill mono">{{ fmtTok(store.totalTokens) }} tok</span>
        </div>
      </header>
      <MessageStream />
      <Composer />
      <div v-if="dragging" class="dropzone" aria-hidden="true">
        <div class="dz-in">松开，文件将上传到会话工作区</div>
      </div>
    </section>
    <OutputDrawer />
  </div>
</template>

<style scoped>
.chat { --conversation-width: 980px; display: flex; height: 100%; min-height: 0; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative; }

/* 拖拽高亮覆层：pointer-events:none 防止覆层自身引发 dragleave 闪灭 */
.dropzone {
  position: absolute; inset: 0; z-index: 30; pointer-events: none;
  display: grid; place-items: center;
  background: rgba(6, 7, 12, 0.55); backdrop-filter: var(--blur);
}
.dz-in {
  padding: 22px 34px; border-radius: 16px; font-size: 14px; color: var(--text-2);
  border: 1.5px dashed rgba(143, 124, 255, 0.55); background: var(--glass-strong);
}
.top {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(6, 7, 12, 0.6), rgba(6, 7, 12, 0.15));
  backdrop-filter: var(--blur);
}
.title { flex: 1; min-width: 0; }
.t {
  font-size: 15px; font-weight: 600; letter-spacing: 0.2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sid { font-family: var(--mono); font-size: 10.5px; color: var(--text-3); margin-top: 1px; }
.pills { display: flex; gap: 8px; }
.mono { font-family: var(--mono); }
.burger {
  display: none; width: 34px; height: 34px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--glass); cursor: pointer; padding: 6px;
}
.burger svg { stroke: var(--text-2); stroke-width: 2; fill: none; stroke-linecap: round; }
.fold {
  display: grid; place-items: center; flex-shrink: 0;
  width: 34px; height: 34px; margin-left: -12px; border-radius: 10px;
  border: 1px solid transparent; background: none; cursor: pointer;
  transition: background 0.18s, border-color 0.18s;
}
.fold:hover { background: var(--glass-hover); border-color: var(--line); }
.fold:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.fold svg {
  width: 18px; height: 18px; stroke: var(--text-2); stroke-width: 1.8; fill: none;
  stroke-linecap: round; stroke-linejoin: round;
}

@media (max-width: 768px) {
  .top { padding: 10px 12px; }
  .burger { display: block; }
  .fold { display: none; }
  .pills .mono { display: none; }
}
</style>
