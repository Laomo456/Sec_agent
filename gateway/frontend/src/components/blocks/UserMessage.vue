<script setup lang="ts">
defineProps<{ text: string; time: string; prompt?: string; sending?: boolean }>();
</script>

<template>
  <div class="row">
    <div class="bubble" :class="{ sending }">
      <div class="text">{{ text }}</div>
      <div class="meta">
        <span v-if="prompt" class="badge">{{ prompt }}</span>
        <span v-if="sending" class="time">发送中…</span>
        <span v-else class="time">{{ time }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row { display: flex; justify-content: flex-end; }
.bubble {
  max-width: 78%;
  position: relative;
  background: linear-gradient(135deg, rgba(143, 124, 255, 0.28), rgba(91, 140, 255, 0.2));
  border: 1px solid rgba(143, 124, 255, 0.38);
  border-radius: 20px 20px 6px 20px;
  padding: 11px 16px 8px;
  box-shadow: 0 8px 30px rgba(91, 80, 220, 0.18);
  backdrop-filter: var(--blur);
  animation: bubble-in 0.35s var(--ease);
  transform-origin: bottom right;
}
.bubble.sending { opacity: 0.7; }
@keyframes bubble-in { from { opacity: 0; transform: scale(0.94) translateY(6px); } to { opacity: 1; transform: none; } }
.text { white-space: pre-wrap; font-size: 14.5px; line-height: 1.65; overflow-wrap: anywhere; }
.meta { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 4px; }
.badge {
  font-size: 10px; color: #d6d0ff; border: 1px solid rgba(214, 208, 255, 0.3);
  border-radius: 999px; padding: 1px 8px;
}
.time { font-size: 10px; color: rgba(236, 238, 247, 0.45); font-family: var(--mono); }
@media (max-width: 768px) { .bubble { max-width: 90%; } }
</style>
