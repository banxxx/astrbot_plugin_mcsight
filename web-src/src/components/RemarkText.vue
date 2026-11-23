<script setup lang="ts">
import type { RemarkRun } from "../lib/types";

defineProps<{
  /** MOTD 彩色分段；null/空时退回纯文本 fallback */
  runs: RemarkRun[] | null | undefined;
  fallback: string;
}>();
</script>

<template>
  <!-- 只渲染文本 + 内联颜色，绝不 innerHTML，MOTD 是不可信输入（防 XSS） -->
  <span v-if="runs && runs.length" class="remark-runs">
    <span v-for="(run, i) in runs" :key="i" :style="run[1] ? { color: run[1] } : undefined">{{ run[0] }}</span>
  </span>
  <span v-else>{{ fallback }}</span>
</template>
