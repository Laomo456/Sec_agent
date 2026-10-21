<script setup lang="ts">
import { computed, ref } from "vue";
import type { GatewayEvent } from "../../types";
import { useSessionStore } from "../../stores/session";

const props = defineProps<{ call: GatewayEvent; result?: GatewayEvent; running?: boolean }>();
const store = useSessionStore();
/** 默认展示输出预览；展开 = 完整预览 + 参数 + 信封 */
const open = ref(false);

/** 解析结果信封。旧会话的预览可能被硬截断成非法 JSON：退回正则抠字段 */
const env = computed<Record<string, any> | null>(() => {
  const raw = props.result?.result;
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? v : { output: String(v) };
  } catch {
    const pick = (k: string) => raw.match(new RegExp(`"${k}":\\s*"((?:[^"\\\\]|\\\\.)*)`))?.[1];
    const unesc = (s?: string) => {
      if (s == null) return undefined;
      // 截断点可能落在转义序列中间（\ 或 \u12）：先去掉残缺尾巴
      const t = s.replace(/\\(u[0-9a-fA-F]{0,3})?$/, "");
      try { return JSON.parse(`"${t}"`); } catch { return t.replace(/\\n/g, "\n"); }
    };
    const out = unesc(pick("output") ?? pick("output_head"));
    return {
      status: pick("status"), summary: unesc(pick("summary")),
      output: out ?? raw, preview_truncated: true,
      stats: { duration_ms: Number(raw.match(/"duration_ms":\s*(\d+)/)?.[1]) || undefined },
    };
  }
});

const status = computed(() => String(env.value?.status ?? ""));
const st = computed(() => {
  // 没等到结果且进程已结束（取消/中断）→ 已中断，别永远转圈
  if (!props.result) return props.running
    ? { txt: "执行中", cls: "run" } : { txt: "已中断", cls: "warn" };
  const s = status.value;
  if (s === "no_progress") return { txt: "重复", cls: "warn" };
  if (["validation_error", "unavailable", "unknown_tool", "not_found"].includes(s))
    return { txt: "被拒", cls: "warn" };
  if (s.startsWith("error") || s === "timeout") return { txt: "失败", cls: "err" };
  return { txt: "完成", cls: "ok" };
});

/** 正文：优先 output，其次 detail/error，最后整个信封 */
const body = computed(() => {
  const e = env.value;
  if (!e) return "";
  // 大输出信封：头部 + 省略提示 + 尾部
  const big = e.output_head != null
    ? [e.output_head, e.stats?.omitted_lines ? `… 省略 ${e.stats.omitted_lines} 行 …` : "", e.output_tail]
        .filter(Boolean).join("\n")
    : undefined;
  const main = e.output ?? big ?? e.detail ?? e.error ?? e.message;
  if (main != null && main !== "") return typeof main === "string" ? main : JSON.stringify(main, null, 2);
  const { status: _s, tool: _t, call_id: _c, stats: _st, ...rest } = e;
  return Object.keys(rest).length ? JSON.stringify(rest, null, 2) : "";
});
const lines = computed(() => body.value.split("\n"));
const PEEK = 6;
const peek = computed(() => lines.value.slice(0, PEEK).join("\n"));
const more = computed(() => lines.value.length > PEEK);
const clipped = computed(() => !!env.value?.preview_truncated || !!env.value?.stats?.truncated);
const totalLines = computed(() => env.value?.stats?.lines as number | undefined);

const argText = computed(() => {
  const p = props.call.params || {};
  const main = (p as any).path ?? (p as any).url ?? (p as any).command ?? (p as any).query;
  return main != null ? String(main) : JSON.stringify(p).slice(0, 80);
});
const hasParams = computed(() => Object.keys(props.call.params || {}).length > 0);
const dur = computed(() => {
  const ms = env.value?.stats?.duration_ms;
  return ms == null ? "" : ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
});
const envText = computed(() => {
  const e = env.value;
  if (!e) return "";
  const { output: _o, output_head: _h, output_tail: _t, ...rest } = e;
  return JSON.stringify(rest, null, 2);
});
</script>

<template>
  <div class="tool" :class="st.cls">
    <button class="bar" :aria-expanded="open" @click="open = !open">
      <span class="ic">
        <svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z" /></svg>
      </span>
      <span class="name">{{ call.tool }}</span>
      <span class="args" :title="argText">{{ argText }}</span>
      <span class="sp" />
      <span v-if="dur" class="dur">{{ dur }}</span>
      <span class="badge"><i />{{ st.txt }}</span>
      <svg class="chev" :class="{ open }" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" /></svg>
    </button>

    <!-- 执行结果：不用点开就能看到 -->
    <div v-if="result" class="out">
      <pre class="code" :class="{ full: open }">{{ open ? body : peek }}<span
        v-if="!body" class="empty">（无输出）</span></pre>
      <div class="foot">
        <button v-if="more" class="link" @click="open = !open">
          {{ open ? "收起" : `展开全部 ${lines.length} 行` }}
        </button>
        <span v-if="clipped" class="hint">
          预览已截断{{ totalLines ? ` · 原始共 ${totalLines} 行` : "" }}
        </span>
        <span class="sp" />
        <button v-if="result.call_id" class="link" @click="store.openDrawer(result.call_id!)">
          查看全量输出 ↗
        </button>
      </div>
    </div>
    <div v-else-if="st.cls === 'run'" class="waiting">等待工具返回…</div>

    <div class="collapse" :class="{ open }">
      <div class="inner">
        <template v-if="hasParams">
          <div class="sec">参数</div>
          <pre class="code">{{ JSON.stringify(call.params ?? {}, null, 2) }}</pre>
        </template>
        <template v-if="envText">
          <div class="sec">元信息</div>
          <pre class="code">{{ envText }}</pre>
        </template>
      </div>
    </div>
    <div v-if="st.cls === 'run'" class="progress" />
  </div>
</template>

<style scoped>
.tool {
  position: relative; overflow: hidden;
  border: 1px solid var(--line); border-radius: 12px;
  background: rgba(255, 255, 255, 0.022);
  animation: rise 0.3s var(--ease);
  transition: border-color 0.25s;
}
.tool.run { border-color: rgba(143, 124, 255, 0.3); }
.bar {
  width: 100%; display: flex; align-items: center; gap: 9px; min-width: 0;
  padding: 8px 12px; border: 0; background: none; cursor: pointer; text-align: left; color: inherit;
}
.bar:hover { background: var(--glass-hover); }
.bar:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.ic {
  width: 24px; height: 24px; border-radius: 7px; flex-shrink: 0;
  display: grid; place-items: center; background: var(--accent-dim);
}
.ic svg { width: 13px; height: 13px; stroke: var(--accent); stroke-width: 1.9; fill: none; stroke-linejoin: round; }
.name { font-family: var(--mono); font-size: 12.5px; color: var(--text); flex-shrink: 0; font-weight: 500; }
.args {
  font-family: var(--mono); font-size: 11.5px; color: var(--text-3);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
}
.sp { flex: 1; }
.dur { font-family: var(--mono); font-size: 10.5px; color: var(--text-3); flex-shrink: 0; }
.badge {
  display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0;
  font-size: 10.5px; border-radius: 999px; padding: 2px 9px;
  border: 1px solid var(--line); color: var(--text-3);
}
.badge i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.ok .badge { color: var(--ok); border-color: rgba(52, 211, 153, 0.3); }
.warn .badge { color: var(--warn); border-color: rgba(251, 191, 36, 0.3); }
.err .badge { color: var(--err); border-color: rgba(248, 113, 113, 0.3); }
.run .badge { color: var(--accent); border-color: rgba(143, 124, 255, 0.35); }
.run .badge i { animation: blink 1s infinite; }
.chev { width: 14px; height: 14px; stroke: var(--text-3); stroke-width: 2; fill: none; transition: transform 0.25s var(--ease); flex-shrink: 0; }
.chev.open { transform: rotate(90deg); }

.out { padding: 0 12px 8px; }
.code {
  background: rgba(3, 4, 10, 0.6); border: 1px solid var(--line); border-radius: 9px;
  padding: 9px 11px; margin: 0; font-family: var(--mono); font-size: 11.5px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow: auto;
  color: var(--text-2);
}
.code.full { max-height: 480px; }
.err .out .code { border-color: rgba(248, 113, 113, 0.28); color: #fecaca; }
.empty { color: var(--text-3); }
.foot { display: flex; align-items: center; gap: 10px; margin-top: 5px; min-height: 16px; }
.hint { font-size: 10.5px; color: var(--text-3); }
.link { border: 0; background: none; padding: 0; color: var(--accent-3); font-size: 11px; cursor: pointer; }
.link:hover { text-decoration: underline; }
.link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
.waiting { padding: 0 12px 10px 45px; font-size: 12px; color: var(--text-3); }

.collapse { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.3s var(--ease); }
.collapse.open { grid-template-rows: 1fr; }
.inner { overflow: hidden; padding: 0 12px; }
.collapse.open .inner { padding-bottom: 10px; }
.sec { font-size: 11px; color: var(--text-3); margin: 6px 0 4px; letter-spacing: 0.5px; }
.progress {
  position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), var(--accent-2), transparent);
  background-size: 200% 100%; animation: shimmer 1.4s linear infinite;
}
</style>
