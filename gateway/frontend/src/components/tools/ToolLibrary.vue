<script setup lang="ts">
/** 工具库：按分类的卡片网格（启停开关）+ 右侧详情（说明/参数表/示例/试运行）+ 检索预览。 */
import { NSwitch } from "naive-ui";
import { computed, ref, watch } from "vue";
import { searchToolsPreview } from "../../api";
import { useToolsStore } from "../../stores/tools";
import type { ToolCard } from "../../types";
import ToolDetail from "./ToolDetail.vue";
import { dangerMeta, kindLabel, sourceLabel } from "./toolMeta";

const emit = defineEmits<{ (e: "edit", name: string): void; (e: "create"): void }>();
const store = useToolsStore();
const q = ref("");
const cat = ref("");
const onlyOn = ref(false);
const selected = ref<string>("");
const sel = computed<ToolCard | undefined>(() => store.byName.get(selected.value));

const shown = computed(() => {
  const kw = q.value.trim().toLowerCase();
  return store.tools.filter((t) => (!cat.value || t.category === cat.value)
    && (!onlyOn.value || t.enabled)
    && (!kw || t.name.includes(kw) || t.description.toLowerCase().includes(kw)
      || (t.keywords || []).some((k) => k.toLowerCase().includes(kw))));
});
const groups = computed(() => {
  const m = new Map<string, ToolCard[]>();
  for (const t of shown.value) m.set(t.category || "misc", [...(m.get(t.category || "misc") || []), t]);
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
});
const stats = computed(() => ({
  total: store.tools.length,
  on: store.tools.filter((t) => t.enabled).length,
  user: store.tools.filter((t) => t.origin === "user").length,
}));

/* 检索预览：模拟模型调用 search_tools 的结果 */
const preview = ref<{ query: string; results: ToolCard[]; hint?: string } | null>(null);
let timer: number | undefined;
watch(q, (v) => {
  clearTimeout(timer);
  if (!v.trim()) { preview.value = null; return; }
  timer = window.setTimeout(async () => {
    try {
      const r = await searchToolsPreview(v, store.effectivePreset);
      preview.value = { query: v, ...r };
    } catch { preview.value = null; }
  }, 450);
});
</script>

<template>
  <div class="lib">
    <div class="left">
      <div class="bar">
        <label class="search">
          <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4-4" /></svg>
          <input v-model="q" placeholder="搜索工具名、描述、关键词…" aria-label="搜索工具" />
        </label>
        <div class="chips">
          <button :class="{ on: !cat }" @click="cat = ''">全部</button>
          <button v-for="c in store.categories" :key="c" :class="{ on: cat === c }" @click="cat = cat === c ? '' : c">{{ c }}</button>
        </div>
        <label class="only"><n-switch v-model:value="onlyOn" size="small" />只看已启用</label>
      </div>

      <div class="stats">
        <span><b>{{ stats.on }}</b> / {{ stats.total }} 已启用</span>
        <span v-if="stats.user"><b>{{ stats.user }}</b> 个自建</span>
        <button class="mk" @click="emit('create')">＋ 新建工具</button>
      </div>

      <!-- 检索预览 -->
      <Transition name="fade">
        <div v-if="preview" class="preview">
          <div class="ph">模型用 <code>search_tools("{{ preview.query }}")</code> 会拿到：</div>
          <div v-if="!preview.results.length" class="pe">{{ preview.hint || "无匹配" }} —— 可以给相关工具补关键词</div>
          <ol v-else>
            <li v-for="r in preview.results" :key="r.name" @click="selected = r.name"><code>{{ r.sig || r.name }}</code></li>
          </ol>
        </div>
      </Transition>

      <div v-if="store.loading && !store.tools.length" class="skel">
        <div v-for="i in 6" :key="i" class="sk" />
      </div>

      <div v-for="[c, list] in groups" :key="c" class="grp">
        <div class="gt">{{ c }} <span>{{ list.length }}</span></div>
        <TransitionGroup name="card" tag="div" class="grid">
          <div v-for="t in list" :key="t.name" class="card" :class="{ off: !t.enabled, sel: selected === t.name }"
               role="button" tabindex="0" @click="selected = t.name" @keydown.enter="selected = t.name">
            <div class="ct">
              <span class="ic" :style="{ '--c': dangerMeta(t.danger).color }">
                <svg viewBox="0 0 24 24"><path :d="dangerMeta(t.danger).icon" /></svg>
              </span>
              <span class="nm">{{ t.name }}</span>
              <n-switch :value="t.enabled" size="small" :aria-label="`启用 ${t.name}`"
                        @click.stop @update:value="(v: boolean) => store.toggle(t.name, v)" />
            </div>
            <div class="ds">{{ t.description }}</div>
            <div class="tags">
              <span class="tag" :style="{ '--c': dangerMeta(t.danger).color }">{{ dangerMeta(t.danger).label }}</span>
              <span class="tag muted">{{ sourceLabel(t) }}</span>
              <span v-if="t.kind && t.kind !== 'script'" class="tag muted">{{ kindLabel[t.kind] }}</span>
            </div>
          </div>
        </TransitionGroup>
      </div>
      <div v-if="store.loaded && !shown.length" class="none">没有匹配的工具</div>
    </div>

    <Transition name="side">
      <ToolDetail v-if="sel" :key="sel.name" :tool="sel" class="right"
                  @close="selected = ''" @edit="emit('edit', sel!.name)" />
    </Transition>
  </div>
</template>

<style scoped>
.lib { display: flex; gap: 18px; max-width: 1240px; align-items: flex-start; }
.left { flex: 1; min-width: 0; }
.right { width: 440px; flex-shrink: 0; position: sticky; top: 0; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.search {
  display: flex; align-items: center; gap: 8px; flex: 1; min-width: 220px;
  padding: 8px 12px; border-radius: 12px; border: 1px solid var(--line); background: var(--glass);
}
.search:focus-within { border-color: rgba(143, 124, 255, 0.5); }
.search svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; fill: none; }
.search input { flex: 1; border: 0; outline: 0; background: none; color: var(--text); font: inherit; font-size: 13px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button {
  border: 1px solid var(--line); background: none; color: var(--text-2); border-radius: 999px;
  padding: 4px 12px; font-size: 12px; cursor: pointer; transition: all 0.2s;
}
.chips button.on { color: var(--text); border-color: rgba(143, 124, 255, 0.5); background: var(--accent-dim); }
.only { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-2); }
.stats { display: flex; align-items: center; gap: 16px; margin: 14px 0 4px; font-size: 12px; color: var(--text-3); }
.stats b { color: var(--text); font-family: var(--mono); }
.mk { margin-left: auto; border: 1px dashed var(--line-2); background: none; color: var(--text-2); border-radius: 10px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.mk:hover { border-color: var(--accent); color: var(--text); }

.preview { margin: 10px 0 4px; padding: 11px 14px; border-radius: 14px; border: 1px solid rgba(45, 212, 191, 0.25); background: rgba(45, 212, 191, 0.05); }
.ph { font-size: 12px; color: var(--text-2); }
.ph code, .preview li code { font-family: var(--mono); font-size: 11.5px; color: var(--accent-2); }
.preview ol { margin: 6px 0 0; padding-left: 20px; }
.preview li { cursor: pointer; font-size: 12px; padding: 2px 0; }
.preview li:hover code { text-decoration: underline; }
.pe { font-size: 12px; color: var(--warn); margin-top: 4px; }

.grp { margin-top: 18px; }
.gt { font-size: 11px; font-weight: 600; letter-spacing: 1px; color: var(--text-3); margin-bottom: 8px; text-transform: uppercase; }
.gt span { font-family: var(--mono); margin-left: 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; }
.card {
  border-radius: 16px; padding: 13px 14px; cursor: pointer; border: 1px solid var(--line);
  background: var(--glass); backdrop-filter: var(--blur); display: flex; flex-direction: column; gap: 8px;
  transition: transform 0.2s var(--ease), border-color 0.2s, opacity 0.2s, box-shadow 0.2s;
}
.card:hover { transform: translateY(-2px); border-color: var(--line-2); }
.card:focus-visible { outline: 2px solid var(--accent); }
.card.sel { border-color: rgba(143, 124, 255, 0.6); box-shadow: 0 0 0 3px rgba(143, 124, 255, 0.14); }
.card.off { opacity: 0.5; }
.card.off .nm { text-decoration: line-through; text-decoration-color: var(--text-3); }
.ct { display: flex; align-items: center; gap: 9px; }
.ic { width: 28px; height: 28px; border-radius: 9px; display: grid; place-items: center; flex-shrink: 0;
  background: color-mix(in srgb, var(--c) 16%, transparent); }
.ic svg { width: 14px; height: 14px; stroke: var(--c); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.nm { flex: 1; min-width: 0; font: 600 13px var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ds { font-size: 12px; color: var(--text-2); line-height: 1.5; min-height: 36px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tags { display: flex; gap: 5px; flex-wrap: wrap; }
.tag { font-size: 10.5px; padding: 1px 8px; border-radius: 999px; color: var(--c); border: 1px solid color-mix(in srgb, var(--c) 35%, transparent); }
.tag.muted { --c: var(--text-3); }
.none { padding: 40px 0; text-align: center; color: var(--text-3); font-size: 13px; }
.skel { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; margin-top: 18px; }
.sk { height: 110px; border-radius: 16px; background: linear-gradient(90deg, var(--glass) 0%, rgba(255,255,255,0.06) 50%, var(--glass) 100%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.card-enter-active, .card-leave-active { transition: all 0.25s var(--ease); }
.card-enter-from, .card-leave-to { opacity: 0; transform: scale(0.96); }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.side-enter-active, .side-leave-active { transition: opacity 0.2s, transform 0.25s var(--ease); }
.side-enter-from, .side-leave-to { opacity: 0; transform: translateX(14px); }
@media (max-width: 1100px) {
  .lib { flex-direction: column; }
  .right { position: fixed; inset: auto 10px 10px 10px; width: auto; max-height: 75vh; z-index: 30; }
}
</style>
