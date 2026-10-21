<script setup lang="ts">
/** 勾选技能的注入块：渲染在用户消息上方，与模型收到的【skill·name】块同源。 */
import { ref } from "vue";

const props = defineProps<{ name: string; content: string }>();
const open = ref(false);
/** 收起时露头两行当预览 */
const preview = (props.content || "").split("\n").slice(0, 2).join("\n");
const long = (props.content || "").length > 120;
</script>

<template>
  <div class="skill" :class="{ open }">
    <button class="head" :aria-expanded="open" @click="open = !open">
      <svg viewBox="0 0 24 24"><path d="M12 3l2.4 5.4 5.6.6-4.2 3.9 1.2 5.7L12 15.8 6.9 18.6l1.2-5.7L4 9l5.6-.6z" /></svg>
      <span class="tag">【skill·{{ name }}】</span>
      <span class="desc" v-if="!open">{{ preview }}</span>
      <svg class="chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    <pre v-if="open" class="body">{{ content }}</pre>
  </div>
</template>

<style scoped>
.skill {
  align-self: center;
  width: 100%;
  max-width: 640px;
  border: 1px solid color-mix(in srgb, var(--accent-2) 30%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, var(--accent-2) 6%, transparent);
  font-size: 12px;
  overflow: hidden;
}
.head {
  display: flex; align-items: center; gap: 8px; width: 100%; box-sizing: border-box;
  padding: 7px 12px; border: 0; background: none; cursor: pointer;
  color: var(--text-2); text-align: left;
}
.head svg { width: 13px; height: 13px; flex-shrink: 0; stroke: var(--accent-2); stroke-width: 1.8; fill: none; stroke-linejoin: round; }
.head .chev { margin-left: auto; stroke: var(--text-3); transition: transform 0.2s var(--ease); }
.skill.open .head .chev { transform: rotate(180deg); }
.tag { font-family: var(--mono); font-size: 11.5px; color: var(--accent-2); white-space: nowrap; }
.desc {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--text-3); font-size: 11px;
}
.body {
  margin: 0; padding: 4px 14px 12px 34px;
  font-family: var(--mono); font-size: 11px; line-height: 1.65;
  color: var(--text-2); white-space: pre-wrap; word-break: break-word;
  max-height: 320px; overflow-y: auto;
}
</style>
