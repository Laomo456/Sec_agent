<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { computed, ref } from "vue";
import type { GatewayEvent } from "../types";
import { useSessionStore } from "../stores/session";
import AssistantMessage from "./blocks/AssistantMessage.vue";
import ToolCard from "./blocks/ToolCard.vue";

const props = defineProps<{ events: GatewayEvent[]; running: boolean; live?: boolean; error?: string }>();
const store = useSessionStore();
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
// 默认展开：执行中/结束后/历史回放都要能直接看到执行了什么；点头部手动收起。
const expanded = ref(true);

const rounds = computed(() => {
  const map = new Map<number, GatewayEvent[]>();
  let cur = 0;
  for (const e of props.events) {
    if (e.type === 'subagent_state' || e.type === 'subagent_notice_applied') continue;
    const it = e.iteration ?? cur;
    cur = it;
    if (!map.has(it)) map.set(it, []);
    map.get(it)!.push(e);
  }
  const list = [...map.entries()].map(([iteration, evts]) => ({
    iteration, events: evts,
    // 只有 model_call（模型还没回话）的轮次没有可渲染内容
    empty: !evts.some((e) => !["model_call", "interject_applied", "interject_withdrawn"].includes(e.type)),
    aborted: false,
  }));
  const last = list[list.length - 1];
  // 结束时最后一轮仍是空的 = 模型调用被取消/出错：明确标出，而不是留一个孤零零的圆点
  if (last?.empty && !props.running) last.aborted = true;
  // 执行中的空轮由下方"实时"轮次展示，避免出现两个圆点
  return list.filter((r) => !r.empty || r.aborted);
});

const nRounds = computed(() => props.events.filter((e) => e.type === "model_call").length);
const nTools = computed(() => props.events.filter((e) => e.type === "tool_call").length);
const nInterject = computed(() => props.events.filter((e) => e.type === "interject").length);
const tokens = computed(() => props.events.filter((e) => e.type === "assistant_message")
  .reduce((s, e) => s + (e.tokens || 0), 0));
const lastType = computed(() => [...props.events].reverse().find(e =>
  e.type !== 'subagent_state' && e.type !== 'subagent_notice_applied')?.type);
const thinking = computed(() => props.running && props.live && lastType.value === "model_call");
const liveHtml = computed(() => md.render(store.live.content || ""));
const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const applied = (id?: string) =>
  props.events.some((e) => e.type === "interject_applied" && e.id === id);
const withdrawn = (id?: string) =>
  props.events.some((e) => e.type === "interject_withdrawn" && e.id === id);

const statusText = computed(() => {
  if (!props.running) return `已完成 · ${nRounds.value} 轮`;
  if (thinking.value) return store.live.content ? "正在回答" : store.live.reasoning ? "深度思考中" : "思考中";
  if (lastType.value === "tool_call") return "调用工具中";
  return `执行中 · 第 ${nRounds.value} 轮`;
});
</script>

<template>
  <div v-if="events.length || running" class="tl" :class="{ running }">
    <button class="head" :aria-expanded="expanded" @click="expanded = !expanded">
      <span class="spinner" v-if="running"><i /></span>
      <svg v-else class="ok" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>
      <span class="title" :class="{ 'shimmer-text': running }">{{ statusText }}</span>
      <span class="stats">
        <span v-if="nTools" class="s">{{ nTools }} 工具</span>
        <span v-if="nInterject" class="s int">{{ nInterject }} 插话</span>
        <span v-if="tokens" class="s">{{ fmtTok(tokens) }} tok</span>
      </span>
      <svg class="chev" :class="{ open: expanded }" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" /></svg>
    </button>

    <div class="collapse" :class="{ open: expanded }">
      <div class="inner">
        <div class="body">
          <div v-for="r in rounds" :key="r.iteration" class="round">
            <span class="pt" :class="{ stop: r.aborted }" />
            <div class="kids">
              <div v-if="r.aborted" class="note warn">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M9 9h6v6H9z" /></svg>
                第 {{ r.iteration }} 轮模型调用未完成：{{ error || "已中断" }}
              </div>
              <template v-for="e in r.events" :key="e.seq">
                <AssistantMessage v-if="e.type === 'assistant_message'" :event="e" bare />
                <ToolCard v-else-if="e.type === 'tool_call'" :call="e"
                          :result="store.toolResults.get(e.seq)" :running="running" />
                <div v-else-if="e.type === 'rollback'" class="note warn">
                  <svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4" /></svg>
                  已回滚：{{ (e.note || "").slice(0, 120) }}
                </div>
                <div v-else-if="e.type === 'interject' && !withdrawn(e.id)" class="note int">
                  <svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z" /></svg>
                  <span class="lab">插话</span>
                  <span class="txt">{{ e.content }}</span>
                  <span class="state">{{ applied(e.id) ? "已送达" : "排队中" }}</span>
                </div>
              </template>
            </div>
          </div>

          <!-- 实时：模型正在思考/输出 -->
          <div v-if="thinking" class="round live">
            <span class="pt pulse" />
            <div class="kids">
              <div v-if="store.live.reasoning" class="live-think">
                {{ store.live.reasoning.slice(-600) }}
              </div>
              <div v-if="store.live.content" class="md live-md" v-html="liveHtml" />
              <div v-if="!store.live.content && !store.live.reasoning" class="dots">
                <i /><i /><i />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tl {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--glass);
  backdrop-filter: var(--blur);
  overflow: hidden;
  transition: border-color 0.3s, box-shadow 0.3s;
}
.tl.running {
  border-color: rgba(143, 124, 255, 0.35);
  box-shadow: 0 0 0 1px rgba(143, 124, 255, 0.08), 0 10px 40px rgba(143, 124, 255, 0.08);
}

.head {
  width: 100%; display: flex; align-items: center; gap: 10px;
  padding: 11px 16px; background: none; border: 0; cursor: pointer;
  color: var(--text-2); font-size: 13px; text-align: left;
}
.head:hover { background: var(--glass-hover); }
.head:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.title { flex: 1; font-weight: 500; }
/* 必须限定在 .head 下：scoped 样式会命中子组件根元素，ToolCard 根是 class="tool ok"，
   裸 .ok 会把整张工具卡压成 16px 的小圆点 */
.head .ok { width: 16px; height: 16px; stroke: var(--ok); stroke-width: 2.4; fill: none; stroke-linecap: round; }
.head .chev { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; fill: none; transition: transform 0.25s var(--ease); }
.head .chev.open { transform: rotate(90deg); }
.spinner {
  width: 16px; height: 16px; border-radius: 50%;
  background: conic-gradient(from 0deg, transparent, var(--accent), var(--accent-2));
  animation: spin 0.9s linear infinite;
  -webkit-mask: radial-gradient(circle, transparent 5px, #000 5.5px);
  mask: radial-gradient(circle, transparent 5px, #000 5.5px);
}
.stats { display: flex; gap: 6px; }
.s {
  font-family: var(--mono); font-size: 10.5px; color: var(--text-3);
  border: 1px solid var(--line); border-radius: 6px; padding: 2px 7px;
}
.s.int { color: var(--accent-2); border-color: rgba(45, 212, 191, 0.3); }

/* 平滑展开：grid 0fr → 1fr */
.collapse { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.35s var(--ease); }
.collapse.open { grid-template-rows: 1fr; }
.inner { overflow: hidden; }
.body {
  border-top: 1px solid var(--line);
  padding: 16px 18px 6px 22px;
  background: rgba(0, 0, 0, 0.12);
}

.round { position: relative; padding-left: 22px; }
.round::before {
  content: ""; position: absolute; left: 4.5px; top: 16px; bottom: 0; width: 1px;
  background: linear-gradient(var(--line-2), transparent);
}
.pt {
  position: absolute; left: 0; top: 6px; width: 10px; height: 10px; border-radius: 50%;
  background: var(--bg-2); border: 2px solid var(--accent);
}
.pt.stop { border-color: var(--warn); }
.pt.pulse { animation: pulse 1.4s infinite; background: var(--accent); }
.kids { display: flex; flex-direction: column; gap: 10px; padding-bottom: 16px; }

.note {
  display: flex; align-items: center; gap: 8px;
  border-radius: 10px; padding: 8px 12px; font-size: 12.5px; line-height: 1.5;
}
.note svg { width: 15px; height: 15px; flex-shrink: 0; stroke: currentColor; stroke-width: 2; fill: none; }
.note.warn { color: var(--warn); background: rgba(251, 191, 36, 0.06); border: 1px solid rgba(251, 191, 36, 0.22); }
.note.int {
  color: var(--text); background: rgba(45, 212, 191, 0.07); border: 1px solid rgba(45, 212, 191, 0.25);
  animation: rise 0.3s var(--ease);
}
.note.int svg { color: var(--accent-2); }
.note .lab { color: var(--accent-2); font-weight: 600; font-size: 11.5px; flex-shrink: 0; }
.note .txt { flex: 1; min-width: 0; white-space: pre-wrap; }
.note .state { font-size: 10.5px; color: var(--text-3); flex-shrink: 0; }

.live-think {
  color: var(--text-3); font-size: 12px; line-height: 1.65; white-space: pre-wrap;
  border-left: 2px solid rgba(143, 124, 255, 0.35); padding-left: 11px;
  max-height: 140px; overflow: hidden;
  mask-image: linear-gradient(transparent, #000 40%);
}
.live-md { font-size: 14px; line-height: 1.75; }
.live-md :deep(> :last-child)::after {
  content: ""; display: inline-block; width: 7px; height: 15px; margin-left: 2px;
  vertical-align: -2px; background: var(--accent); border-radius: 1px;
  animation: blink 1s steps(2) infinite;
}
.dots { display: flex; gap: 5px; padding: 6px 0; }
.dots i {
  width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
  animation: bounce 1.2s var(--ease) infinite;
}
.dots i:nth-child(2) { animation-delay: 0.15s; background: #5b8cff; }
.dots i:nth-child(3) { animation-delay: 0.3s; background: var(--accent-2); }
@keyframes bounce { 0%, 60%, 100% { transform: translateY(0); opacity: 0.5; } 30% { transform: translateY(-5px); opacity: 1; } }

@media (max-width: 768px) {
  .stats .s:not(.int) { display: none; }
  .body { padding: 12px 12px 4px 14px; }
}
</style>
