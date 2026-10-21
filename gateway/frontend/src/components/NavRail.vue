<script setup lang="ts">
import { useSessionStore } from "../stores/session";

type View = "chat" | "workflow" | "tools" | "knowledge" | "settings";
defineProps<{ view: View }>();
const emit = defineEmits<{ (e: "change", v: View): void }>();
const store = useSessionStore();

const items: { key: View; label: string; icon: string }[] = [
  { key: "chat", label: "对话", icon: "M4 5h16v11H8l-4 4z" },
  { key: "workflow", label: "工作流",
    icon: "M5 4h5v5H5zM14 15h5v5h-5zM7.5 9v3.5a2 2 0 0 0 2 2H14M14 4h5v5h-5zM16.5 9v6" },
  { key: "tools", label: "工具",
    icon: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z" },
  { key: "knowledge", label: "知识库",
    icon: "M12 6.5C10.6 5 8.4 4.5 5 4.5v14c3.4 0 5.6.5 7 2 1.4-1.5 3.6-2 7-2v-14c-3.4 0-5.6.5-7 2zM12 6.5v14" },
  { key: "settings", label: "设置",
    icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" },
];
</script>

<template>
  <nav class="rail glass" aria-label="主导航">
    <div class="logo" title="Agent Gateway">
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <defs>
          <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#8f7cff" /><stop offset=".55" stop-color="#5b8cff" />
            <stop offset="1" stop-color="#2dd4bf" />
          </linearGradient>
        </defs>
        <path d="M16 3 28 10v12L16 29 4 22V10z" fill="none" stroke="url(#lg)" stroke-width="2.2"
              stroke-linejoin="round" />
        <circle cx="16" cy="16" r="4.2" fill="url(#lg)" />
      </svg>
    </div>

    <div class="items">
      <button v-for="it in items" :key="it.key" class="item" :class="{ active: view === it.key }"
              :aria-current="view === it.key ? 'page' : undefined" :aria-label="it.label"
              @click="emit('change', it.key)">
        <span class="glow" />
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="it.icon" /></svg>
        <span class="lab">{{ it.label }}</span>
      </button>
    </div>

    <div class="status" :title="store.connected ? '网关在线' : '网关离线'">
      <span class="dot" :class="{ on: store.connected }" />
    </div>
  </nav>
</template>

<style scoped>
.rail {
  width: 72px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 0 14px;
  border-width: 0 1px 0 0;
  background: rgba(8, 9, 16, 0.55);
}
.logo { width: 36px; height: 36px; margin-bottom: 26px; }
.logo svg { width: 100%; height: 100%; filter: drop-shadow(0 0 10px rgba(143, 124, 255, 0.45)); }
.logo:hover svg { animation: spin 6s linear infinite; }

.items { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.item {
  position: relative;
  width: 54px;
  padding: 9px 0 7px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  transition: color 0.2s, background 0.2s, transform 0.2s var(--ease);
}
.item svg {
  width: 21px; height: 21px; fill: none; stroke: currentColor;
  stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round;
  position: relative;
}
.item .lab { font-size: 10.5px; letter-spacing: 0.5px; position: relative; }
.item:hover { color: var(--text); background: var(--glass-hover); }
.item:active { transform: scale(0.94); }
.item:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.item .glow {
  position: absolute; inset: 0; border-radius: inherit; opacity: 0;
  background: linear-gradient(160deg, rgba(143, 124, 255, 0.28), rgba(45, 212, 191, 0.12));
  box-shadow: inset 0 0 0 1px rgba(143, 124, 255, 0.35);
  transition: opacity 0.25s;
}
.item.active { color: #fff; }
.item.active .glow { opacity: 1; }
.item.active::before {
  content: ""; position: absolute; left: -9px; top: 12px; bottom: 12px; width: 3px;
  border-radius: 2px; background: var(--accent-grad);
  box-shadow: 0 0 12px var(--accent);
}

.status { padding-top: 10px; }
.dot { display: block; width: 8px; height: 8px; border-radius: 50%; background: var(--err); }
.dot.on { background: var(--ok); box-shadow: 0 0 10px var(--ok); animation: blink 2.4s infinite; }

@media (max-width: 768px) {
  .rail {
    /* box-sizing 必须显式给:项目没有全局 border-box,content-box 下 100%+20px padding = 395px,右侧 tab 被裁 */
    box-sizing: border-box;
    width: 100%; height: 60px; flex-direction: row; padding: 0 10px;
    border-width: 1px 0 0 0;
  }
  .logo, .status { display: none; }
  .items { flex-direction: row; justify-content: space-around; width: 100%; }
  .item { width: 72px; }
  .item.active::before { display: none; }
}
</style>
