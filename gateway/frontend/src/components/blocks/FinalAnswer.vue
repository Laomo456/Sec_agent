<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { useMessage } from "naive-ui";
import { computed, onMounted, ref } from "vue";

const props = defineProps<{ text: string }>();
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const html = computed(() => md.render(props.text));
const msg = useMessage();
const root = ref<HTMLElement>();

async function copy() {
  await navigator.clipboard.writeText(props.text);
  msg.success("已复制");
}

/** 给代码块加复制按钮 */
onMounted(() => {
  root.value?.querySelectorAll("pre").forEach((pre) => {
    const b = document.createElement("button");
    b.className = "code-copy";
    b.textContent = "复制";
    b.onclick = async () => {
      await navigator.clipboard.writeText(pre.innerText.replace(/复制$/, ""));
      b.textContent = "已复制";
      setTimeout(() => (b.textContent = "复制"), 1400);
    };
    pre.appendChild(b);
  });
});
</script>

<template>
  <div class="final">
    <div class="avatar" aria-hidden="true"><span /></div>
    <div class="content">
      <div ref="root" class="md body" v-html="html" />
      <div class="actions">
        <button class="act" aria-label="复制回答" @click="copy">
          <svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></svg>
          复制
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.final { display: flex; gap: 14px; animation: rise 0.4s var(--ease); }
.avatar {
  flex-shrink: 0; width: 30px; height: 30px; border-radius: 10px; margin-top: 2px;
  background: var(--accent-grad); display: grid; place-items: center;
  box-shadow: 0 4px 16px rgba(143, 124, 255, 0.35);
}
.avatar span { width: 10px; height: 10px; border-radius: 50%; background: #0b0d16; }
.content { flex: 1; min-width: 0; }
.body { font-size: 14.5px; line-height: 1.8; }
.body :deep(pre) { position: relative; }
.body :deep(.code-copy) {
  position: absolute; top: 8px; right: 8px;
  border: 1px solid var(--line-2); background: rgba(20, 22, 36, 0.9);
  color: var(--text-2); font-size: 11px; border-radius: 6px; padding: 2px 8px;
  cursor: pointer; opacity: 0; transition: opacity 0.2s; font-family: var(--sans);
}
.body :deep(pre:hover .code-copy) { opacity: 1; }
.actions { display: flex; gap: 6px; margin-top: 6px; opacity: 0.55; transition: opacity 0.2s; }
.final:hover .actions { opacity: 1; }
.act {
  display: inline-flex; align-items: center; gap: 5px;
  border: 0; background: none; color: var(--text-3); font-size: 11.5px;
  padding: 4px 8px; border-radius: 7px; cursor: pointer;
}
.act:hover { background: var(--glass-hover); color: var(--text); }
.act svg { width: 13px; height: 13px; stroke: currentColor; stroke-width: 1.8; fill: none; }
@media (max-width: 768px) { .avatar { display: none; } }
</style>
