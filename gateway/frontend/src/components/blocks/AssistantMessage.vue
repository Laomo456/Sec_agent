<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { computed, ref } from "vue";
import type { GatewayEvent } from "../../types";

const props = defineProps<{ event: GatewayEvent; bare?: boolean }>();
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const html = computed(() => md.render(props.event.content || ""));
const hasText = computed(() => !!(props.event.content || "").trim());
const openThink = ref(false);
</script>

<template>
  <div class="am">
    <div v-if="event.reasoning" class="think">
      <button class="ts" :aria-expanded="openThink" @click="openThink = !openThink">
        <svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z" /></svg>
        思考过程 · {{ event.reasoning.length }} 字
        <svg class="chev" :class="{ open: openThink }" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" /></svg>
      </button>
      <div class="collapse" :class="{ open: openThink }">
        <div class="inner"><div class="tbody">{{ event.reasoning }}</div></div>
      </div>
    </div>
    <div v-if="hasText" class="md body" v-html="html" />
    <div v-if="!bare" class="meta">轮 {{ event.iteration }} · {{ event.tokens ?? "?" }} tokens</div>
  </div>
</template>

<style scoped>
.am { min-width: 0; }
.think { margin-bottom: 6px; }
.ts {
  display: inline-flex; align-items: center; gap: 6px;
  border: 0; background: none; padding: 2px 0; cursor: pointer;
  color: var(--text-3); font-size: 12px;
}
.ts:hover { color: var(--text-2); }
.ts svg { width: 14px; height: 14px; stroke: currentColor; stroke-width: 1.8; fill: none; }
.chev { transition: transform 0.25s var(--ease); }
.chev.open { transform: rotate(90deg); }
.collapse { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.3s var(--ease); }
.collapse.open { grid-template-rows: 1fr; }
.inner { overflow: hidden; }
.tbody {
  color: var(--text-3); font-size: 12.5px; white-space: pre-wrap; line-height: 1.7;
  border-left: 2px solid rgba(143, 124, 255, 0.3); padding: 4px 0 4px 12px; margin-top: 6px;
}
.body { font-size: 14px; line-height: 1.75; color: var(--text); }
.meta { color: var(--text-3); font-size: 10.5px; margin-top: 4px; font-family: var(--mono); }
</style>
