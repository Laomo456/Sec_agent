<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { Bot, ChevronRight, FileText, LoaderCircle, Square, X } from 'lucide-vue-next';
import { stopSubagent, getSubagents, subagentReportUrl } from '../api';
import { useSessionStore } from '../stores/session';
import { useWorkflowStore } from '../stores/workflow';
import type { SubagentJob } from '../types';

const props = defineProps<{ view: string }>();
const session = useSessionStore();
const workflow = useWorkflowStore();
const sid = computed(() => props.view === 'workflow'
  ? (workflow.runId ? `wf-${workflow.runId}` : '')
  : props.view === 'tools' ? 'tool-playground' : session.currentId);
const jobs = ref<SubagentJob[]>([]);
const hover = ref(false);
const focused = ref(false);
const pinned = ref(false);
const open = computed(() => hover.value || focused.value || pinned.value);
const clock = ref(Date.now());
const error = ref('');
const stopping = ref(new Set<string>());
const active = (j: SubagentJob) => ['queued', 'running', 'writing'].includes(j.status);
const running = computed(() => jobs.value.filter(active).length);
const finished = computed(() => jobs.value.length - running.value);
const sorted = computed(() => [...jobs.value].sort((a, b) =>
  Number(active(b)) - Number(active(a)) || b.created_at.localeCompare(a.created_at)));
const labels: Record<string, string> = {
  queued: '排队中', running: '执行中', writing: '写入报告', ok: '已完成',
  error: '失败', incomplete: '未完成', timeout: '超时', cancelled: '已取消', interrupted: '已中断',
  starting: '启动中', model_call: '思考中', tool_call: '调用工具', tool_result: '工具已返回',
  assistant_message: '整理结果', finished: '',
};
function duration(j: SubagentJob) {
  if (!j.started_at) return '未开始';
  const ms = active(j) ? clock.value - Date.parse(j.started_at) : j.duration_ms;
  const sec = Math.max(0, Math.floor(ms / 1000));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}
let generation = 0;
let busy = false;
async function refresh() {
  if (!sid.value || busy) return;
  const id = sid.value;
  const current = generation;
  busy = true;
  try {
    const data = await getSubagents(id);
    if (current === generation) {
      const live = new Map(jobs.value.map(j => [j.agent_id, j]));
      jobs.value = data.jobs.map(j => {
        const old = live.get(j.agent_id);
        return old && old.updated_at > j.updated_at ? old : j;
      });
      error.value = '';
    }
  } catch (e: any) {
    if (current === generation) error.value = e.message || '状态加载失败';
  } finally { busy = false; }
}
watch(sid, () => { generation++; jobs.value = []; pinned.value = false; refresh(); }, { immediate: true });
// Durable snapshots also recover live updates after reconnect or workflow completion.
const timer = window.setInterval(() => { clock.value = Date.now(); refresh(); }, 1500);
watch(() => session.events.length, () => {
  if (sid.value !== session.currentId) return;
  const evt = session.events[session.events.length - 1];
  if (evt?.type !== 'subagent_state') return;
  const record = evt as unknown as SubagentJob;
  if (!record.batch_id) return;
  const index = jobs.value.findIndex(j => j.agent_id === record.agent_id);
  if (index < 0) jobs.value.push(record);
  else if (record.updated_at >= jobs.value[index].updated_at) jobs.value[index] = record;
});
async function stop(job: SubagentJob) {
  stopping.value.add(job.agent_id);
  try { await stopSubagent(sid.value, job.agent_id); await refresh(); }
  catch (e: any) { error.value = e.message; }
  finally { stopping.value.delete(job.agent_id); }
}
function close() {
  pinned.value = hover.value = focused.value = false;
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
}
onUnmounted(() => { clearInterval(timer); generation++; });
</script>

<template>
  <aside v-if="sid" class="agent-island" :class="{ expanded: open }"
         aria-label="子智能体状态" @mouseenter="hover = true" @mouseleave="hover = false"
         @focusin="focused = true" @focusout="focused = !!($event.relatedTarget && $el.contains($event.relatedTarget))"
         @keydown.esc.prevent="close">
    <button class="island-trigger" :aria-expanded="open" aria-controls="subagent-panel"
            title="子智能体状态" aria-label="子智能体状态" @click="pinned = !pinned">
      <Bot :size="21" />
      <span class="island-count" :class="{ active: running }">{{ running || finished }}</span>
      <span class="signal" :class="{ active: running }" />
    </button>
    <section v-if="open" id="subagent-panel" class="island-panel">
      <header>
        <div><strong>子智能体</strong><span>{{ running }} 执行中 · {{ finished }} 已结束</span></div>
        <button class="icon-button" title="收起" aria-label="收起" @click="close"><X :size="17" /></button>
      </header>
      <p v-if="error" class="island-error" role="alert">{{ error }}</p>
      <div class="job-list">
        <p v-if="!jobs.length" class="empty">暂无子任务</p>
        <article v-for="job in sorted" :key="job.agent_id" class="job" :data-status="job.status">
          <div class="job-head">
            <LoaderCircle v-if="active(job)" class="spin" :size="16" />
            <Bot v-else :size="16" />
            <strong :title="job.name">{{ job.name }}</strong>
            <span class="status">{{ labels[job.status] }}</span>
          </div>
          <p class="job-task">{{ job.task }}</p>
          <div class="job-meta">
            <span>{{ duration(job) }}</span><span>{{ job.iterations }} 轮 · {{ job.tool_calls }} 工具</span>
            <span v-if="active(job)">{{ labels[job.phase] || job.phase }}</span>
          </div>
          <p v-if="job.detail" class="job-detail">{{ job.detail }}</p>
          <footer>
            <a v-if="!active(job)" :href="subagentReportUrl(sid, job.agent_id)" target="_blank" rel="noopener"
               title="打开报告"><FileText :size="15" /><span>报告</span><ChevronRight :size="14" /></a>
            <span v-else class="job-id">{{ job.agent_id.slice(-8) }}</span>
            <button v-if="active(job)" class="icon-button stop" title="停止子任务" aria-label="停止子任务"
                    :disabled="stopping.has(job.agent_id)" @click="stop(job)"><Square :size="14" /></button>
          </footer>
        </article>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.agent-island { position: fixed; z-index: 90; right: 12px; top: 90px; display: flex; align-items: flex-start; gap: 8px; max-width: calc(100vw - 24px); }
.island-trigger { order: 2; width: 48px; height: 86px; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; border-radius: 24px; border: 1px solid #394342; background: #151c1b; color: #b8d4cf; cursor: pointer; box-shadow: 0 8px 25px #0005; }
.island-count { font: 12px var(--mono); color: #a1abaa; }
.island-count.active { color: #68deba; }
.signal { width: 4px; height: 4px; border-radius: 50%; background: #66716e; }
.signal.active { background: #68deba; animation: glow 1.5s ease-in-out infinite; }
.island-panel { width: 340px; min-width: 0; max-height: calc(100dvh - 190px); display: flex; flex-direction: column; background: #171b20; border: 1px solid #36423f; border-radius: 8px; box-shadow: 0 15px 50px #0008; animation: appear .18s ease-out; overflow: hidden; }
header { display: flex; align-items: center; justify-content: space-between; padding: 16px; border-bottom: 1px solid #30363b; }
header div { display: flex; flex-direction: column; gap: 5px; }
header strong { font-size: 14px; color: #eef3f1; }
header span { font-size: 11px; color: #95a29e; }
.icon-button { width: 30px; height: 30px; flex-shrink: 0; display: grid; place-items: center; border: 0; border-radius: 6px; color: #b8c2bf; background: transparent; cursor: pointer; }
.icon-button:hover { background: #ffffff10; }
button:focus-visible, a:focus-visible { outline: 2px solid #68deba; outline-offset: 2px; }
.job-list { overflow: auto; overscroll-behavior: contain; padding: 0 16px; }
.job { padding: 14px 0; border-bottom: 1px solid #30363b; }
.job:last-child { border-bottom: 0; }
.job-head { display: flex; align-items: center; gap: 8px; color: #a3b7b0; }
.job-head strong { flex: 1; min-width: 0; font-size: 13px; color: #e3ebe7; overflow-wrap: anywhere; }
.status { flex-shrink: 0; font-size: 11px; color: #68deba; }
.job[data-status="error"] .status, .job[data-status="timeout"] .status, .job[data-status="incomplete"] .status, .job[data-status="interrupted"] .status { color: #f3bb75; }
.job[data-status="queued"] .status, .job[data-status="cancelled"] .status { color: #a2a9b0; }
.job-task { margin: 9px 0; font-size: 12px; line-height: 1.6; color: #a9b4b0; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.job-meta { display: flex; flex-wrap: wrap; gap: 5px 12px; color: #8b9894; font-size: 10px; font-variant-numeric: tabular-nums; }
.job-detail, .island-error { font-size: 12px; color: #f3bb75; overflow-wrap: anywhere; }
.island-error { margin: 12px 16px 0; }
footer { display: flex; align-items: center; justify-content: space-between; min-height: 30px; margin-top: 6px; }
footer a { display: flex; align-items: center; gap: 5px; color: #8bd4c3; font-size: 11px; text-decoration: none; }
.job-id { font: 10px var(--mono); color: #75847e; }
.stop { color: #f1a69c; }
.stop:disabled { opacity: .4; cursor: wait; }
.empty { padding: 22px 0; text-align: center; font-size: 12px; color: #8b9894; }
.spin { flex-shrink: 0; animation: rotate 2s linear infinite; }
@keyframes rotate { to { transform: rotate(360deg); } }
@keyframes glow { 50% { opacity: .35; } }
@keyframes appear { from { opacity: 0; transform: translateX(6px); } }
@media (max-width: 768px) {
  .agent-island { right: 8px; top: 68px; max-width: calc(100vw - 16px); gap: 6px; }
  .island-trigger { width: 38px; height: 76px; }
  .island-panel { width: min(320px, calc(100vw - 60px)); max-height: calc(100dvh - 200px); }
  header { padding: 12px; }
  .job-list { padding: 0 12px; }
}
@media (prefers-reduced-motion: reduce) { .spin, .signal, .island-panel { animation: none; } }
</style>
