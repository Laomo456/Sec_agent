<script setup lang="ts">
import { NInput, NPopover } from "naive-ui";
import { computed, nextTick, ref, watch } from "vue";
import { useSkillsStore } from "../stores/skills";

const skills = useSkillsStore();
const open = ref(false);
const trigger = ref<HTMLButtonElement>();
const panelGap = ref(48);
const query = ref("");
const loadError = ref(false);
async function load() {
  loadError.value = false;
  try { await skills.load(); }
  catch { loadError.value = true; }
}
load();
watch(open, async (value) => {
  if (!value) { query.value = ""; return; }
  await nextTick();
  const button = trigger.value;
  const card = button?.closest(".card");
  if (button && card) panelGap.value = Math.max(6, button.getBoundingClientRect().top - card.getBoundingClientRect().top + 8);
});
const filtered = computed(() => {
  const term = query.value.trim().toLowerCase();
  return skills.skills.filter((skill) => !term || `${skill.name} ${skill.description || ""}`.toLowerCase().includes(term));
});
</script>

<template>
  <n-popover v-model:show="open" trigger="click" placement="top-start" :show-arrow="false"
             :width="340" :theme-overrides="{ space: `${panelGap}px` }" :style="{ maxWidth: 'calc(100vw - 24px)', padding: '12px', boxSizing: 'border-box' }">
    <template #trigger>
      <button ref="trigger" type="button" class="skill-trigger" :class="{ selected: skills.selected.length, active: open }"
              aria-label="选择技能" aria-haspopup="dialog" :aria-expanded="open" @keydown.esc.stop="open = false">
        <svg class="skill-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.5 5.6 6.1.6-4.6 4.1 1.4 6L12 16.2 6.6 19.3l1.4-6-4.6-4.1 6.1-.6Z" /></svg>
        <span>技能</span>
        <span v-if="skills.selected.length" class="count">{{ skills.selected.length }}</span>
        <svg class="chevron" :class="{ open }" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
      </button>
    </template>
    <section class="skill-panel" role="dialog" aria-label="选择技能" @keydown.esc.stop="open = false">
      <div class="skill-heading">
        <span>选择技能 <small>{{ skills.selected.length ? `已选 ${skills.selected.length} 项` : "可多选" }}</small></span>
        <button type="button" class="clear" :disabled="!skills.selected.length" @click="skills.selected = []">清空</button>
      </div>
      <n-input v-model:value="query" size="small" clearable placeholder="搜索技能名称或描述" aria-label="搜索技能" />
      <div class="skill-options" role="group" aria-label="可选技能">
        <label v-for="skill in filtered" :key="skill.name" class="skill-option"
               :class="{ checked: skills.selected.includes(skill.name) }">
          <input type="checkbox" :checked="skills.selected.includes(skill.name)" @change="skills.toggle(skill.name)" />
          <span class="skill-copy">
            <span class="skill-name">{{ skill.name }}</span>
            <span v-if="skill.description" class="skill-description" :title="skill.description">{{ skill.description }}</span>
          </span>
        </label>
        <div v-if="loadError" class="empty">技能加载失败 <button type="button" class="retry" @click="load">重试</button></div>
        <div v-else-if="!skills.loaded" class="empty">正在加载技能…</div>
        <div v-else-if="!filtered.length" class="empty">{{ query.trim() ? "没有匹配的技能" : "暂无技能，可在设置中添加" }}</div>
      </div>
    </section>
  </n-popover>
</template>

<style scoped>
.skill-trigger {
  display: inline-flex; align-items: center; gap: 7px; flex: 0 0 auto;
  height: 28px; padding: 0 10px; border-radius: 9px;
  border: 1px solid var(--line); background: rgba(255, 255, 255, 0.04);
  font: inherit; font-size: 12px; color: var(--text-2); cursor: pointer;
  transition: border-color 0.18s, background 0.18s;
}
.skill-trigger:hover, .skill-trigger.active { border-color: var(--accent); background: var(--glass-hover); }
.skill-trigger.selected { color: var(--text); }
.skill-trigger:focus-visible, .clear:focus-visible, .retry:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.skill-trigger svg { width: 14px; height: 14px; flex-shrink: 0; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.skill-icon { opacity: 0.65; }
.chevron { margin-left: 1px; opacity: 0.5; transition: transform 0.18s; }
.chevron.open { transform: rotate(180deg); }
.count { min-width: 16px; padding: 0 4px; box-sizing: border-box; border-radius: 5px; background: rgba(143, 124, 255, 0.2); color: var(--accent); font-size: 10px; line-height: 18px; text-align: center; }
.skill-panel { min-width: 0; }
.skill-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 13px; color: var(--text); }
.skill-heading small { margin-left: 6px; font-size: 11px; color: var(--text-3); }
.clear, .retry { border: 0; padding: 2px 0; background: none; color: var(--accent); font: inherit; font-size: 11px; cursor: pointer; }
.clear:disabled { color: var(--text-3); cursor: default; opacity: 0.5; }
.skill-options { max-height: min(300px, 42dvh); overflow-y: auto; overscroll-behavior: contain; margin-top: 8px; scrollbar-width: thin; }
.skill-option { display: flex; align-items: flex-start; gap: 9px; padding: 9px 8px; border-radius: 8px; cursor: pointer; }
.skill-option:hover { background: var(--glass-hover); }
.skill-option.checked { background: rgba(143, 124, 255, 0.08); }
.skill-option input { flex: 0 0 auto; margin: 3px 0 0; accent-color: var(--accent); cursor: pointer; }
.skill-copy { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.skill-name { color: var(--text-2); font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.skill-description { color: var(--text-3); font-size: 11px; line-height: 1.5; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.empty { padding: 20px 8px; color: var(--text-3); font-size: 12px; text-align: center; }
</style>
