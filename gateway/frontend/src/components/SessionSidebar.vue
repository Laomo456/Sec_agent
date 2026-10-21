<script setup lang="ts">
import { NPopconfirm, NScrollbar } from "naive-ui";
import { computed, ref } from "vue";
import { useSessionStore } from "../stores/session";
import type { SessionInfo } from "../types";

defineProps<{ open: boolean; collapsed?: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();
const store = useSessionStore();
const q = ref("");

const clock = (id: string) => {
  const m = id.match(/(\d{8})-?(\d{6})/);
  return m ? `${m[1].slice(4, 6)}/${m[1].slice(6)} ${m[2].slice(0, 2)}:${m[2].slice(2, 4)}` : "";
};

/** 按"今天 / 最近 7 天 / 更早"分组，像 Claude 的侧栏 */
const groups = computed(() => {
  const kw = q.value.trim().toLowerCase();
  const list = store.sessions.filter((s) =>
    !kw || (s.title || "").toLowerCase().includes(kw) || s.session_id.includes(kw));
  const now = Date.now() / 1000;
  const buckets: Record<string, SessionInfo[]> = { 今天: [], "最近 7 天": [], 更早: [] };
  for (const s of list) {
    const age = now - (s.updated || 0);
    (age < 86400 ? buckets["今天"] : age < 7 * 86400 ? buckets["最近 7 天"] : buckets["更早"]).push(s);
  }
  return Object.entries(buckets).filter(([, v]) => v.length);
});

const rows = computed(() => groups.value.flatMap(([label, items]) => [
  { key: `h-${label}`, head: label, s: null as SessionInfo | null },
  ...items.map((s) => ({ key: s.session_id, head: "", s })),
]));

function pick(sid: string) {
  store.open(sid);
  emit("close");
}
</script>

<template>
  <div class="backdrop" :class="{ show: open }" @click="emit('close')" />
  <aside class="side glass" :class="{ open, collapsed }" :inert="(collapsed && !open) || undefined">
    <button class="new" @click="store.newSession(); emit('close')">
      <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>
      新建对话
      <kbd>N</kbd>
    </button>

    <label class="search">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4-4" /></svg>
      <input v-model="q" placeholder="搜索对话" aria-label="搜索对话" />
    </label>

    <n-scrollbar class="list">
      <TransitionGroup name="list" tag="div">
        <div v-for="row in rows" :key="row.key" :class="row.head ? 'sec' : 'item'"
             v-bind="row.head ? {} : { role: 'button', tabindex: 0 }"
             :data-active="!row.head && row.s!.session_id === store.currentId"
             @click="!row.head && pick(row.s!.session_id)"
             @keydown.enter="!row.head && pick(row.s!.session_id)">
          <template v-if="row.head">{{ row.head }}</template>
          <template v-else>
            <div class="info">
              <div class="name">{{ row.s!.title || "新对话" }}</div>
              <div class="time">{{ clock(row.s!.session_id) }}</div>
            </div>
            <n-popconfirm @positive-click="store.removeSession(row.s!.session_id)">
              <template #trigger>
                <button class="del" aria-label="删除会话" @click.stop>
                  <svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" /></svg>
                </button>
              </template>
              删除该会话及全部记录？不可恢复。
            </n-popconfirm>
          </template>
        </div>
      </TransitionGroup>
      <div v-if="!groups.length" class="empty">没有匹配的对话</div>
    </n-scrollbar>
  </aside>
</template>

<style scoped>
.side {
  width: 264px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 12px 12px;
  border-width: 0 1px 0 0;
  background: rgba(10, 11, 19, 0.5);
  overflow: hidden;
  transition: width 0.28s var(--ease), padding 0.28s var(--ease), opacity 0.2s, border-color 0.28s;
}
/* 收起时内容保持原宽度，只裁掉，避免文字被挤成一列 */
.side > * { min-width: 240px; }
@media (min-width: 769px) {
  .side.collapsed {
    width: 0; padding-left: 0; padding-right: 0;
    border-right-color: transparent; opacity: 0;
  }
}
.new {
  display: flex; align-items: center; gap: 9px;
  padding: 10px 14px; margin-bottom: 12px;
  border-radius: 12px; border: 1px solid rgba(143, 124, 255, 0.35);
  background: linear-gradient(135deg, rgba(143, 124, 255, 0.22), rgba(45, 212, 191, 0.08));
  color: var(--text); font-size: 13px; font-weight: 500; cursor: pointer;
  transition: transform 0.2s var(--ease), box-shadow 0.25s;
}
.new:hover { box-shadow: var(--accent-glow); transform: translateY(-1px); }
.new:active { transform: scale(0.98); }
.new svg { width: 16px; height: 16px; stroke: currentColor; stroke-width: 2; fill: none; stroke-linecap: round; }
.new kbd {
  margin-left: auto; font-family: var(--mono); font-size: 10px; color: var(--text-3);
  border: 1px solid var(--line-2); border-radius: 5px; padding: 0 5px;
}

.search {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 11px; margin-bottom: 10px;
  border-radius: 10px; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.025);
}
.search:focus-within { border-color: rgba(143, 124, 255, 0.45); }
.search svg { width: 14px; height: 14px; stroke: var(--text-3); stroke-width: 2; fill: none; flex-shrink: 0; }
.search input {
  flex: 1; min-width: 0; border: 0; outline: 0; background: none;
  color: var(--text); font-size: 12.5px; font-family: inherit;
}

.list { flex: 1; min-height: 0; }
.sec {
  padding: 12px 10px 6px; font-size: 10.5px; color: var(--text-3);
  letter-spacing: 1px; font-weight: 600;
}
.item {
  position: relative;
  display: flex; align-items: center; gap: 4px;
  padding: 8px 10px; margin: 1px 0;
  border-radius: 10px; cursor: pointer;
  transition: background 0.18s;
}
.item:hover { background: var(--glass-hover); }
.item:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.item[data-active="true"] {
  background: linear-gradient(90deg, rgba(143, 124, 255, 0.18), rgba(143, 124, 255, 0.04));
}
.item[data-active="true"]::before {
  content: ""; position: absolute; left: 0; top: 9px; bottom: 9px; width: 2.5px;
  border-radius: 2px; background: var(--accent-grad);
}
.info { flex: 1; min-width: 0; }
.name {
  font-size: 13px; color: var(--text-2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.item[data-active="true"] .name { color: var(--text); }
.time { font-size: 10px; color: var(--text-3); margin-top: 2px; font-family: var(--mono); }
.del {
  opacity: 0; border: 0; background: none; padding: 5px; border-radius: 7px; cursor: pointer;
  transition: opacity 0.15s;
}
.del svg { width: 14px; height: 14px; stroke: var(--text-3); stroke-width: 1.8; fill: none; }
.item:hover .del, .del:focus-visible { opacity: 1; }
.del:hover { background: rgba(248, 113, 113, 0.12); }
.del:hover svg { stroke: var(--err); }
.empty { padding: 30px 0; text-align: center; color: var(--text-3); font-size: 12px; }

.list-enter-active, .list-leave-active { transition: all 0.25s var(--ease); }
.list-enter-from, .list-leave-to { opacity: 0; transform: translateX(-8px); }

.backdrop { display: none; }
@media (max-width: 768px) {
  .backdrop {
    display: block; position: fixed; inset: 0; z-index: 40;
    background: rgba(0, 0, 0, 0.55); opacity: 0; pointer-events: none; transition: opacity 0.2s;
  }
  .backdrop.show { opacity: 1; pointer-events: auto; }
  .side {
    position: fixed; top: 0; bottom: 0; left: 0; z-index: 41;
    width: min(290px, 86vw); transform: translateX(-100%); visibility: hidden;
    transition: transform 0.25s var(--ease), visibility 0.25s;
    background: var(--glass-strong);
  }
  .side.open { transform: none; visibility: visible; }
  .del { opacity: 1; }
}
</style>
