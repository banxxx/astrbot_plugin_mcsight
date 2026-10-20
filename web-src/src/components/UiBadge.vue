<script setup lang="ts">
withDefaults(defineProps<{ tone?: "neutral" | "off" | "ok" | "warn" | "port" }>(), {
  tone: "neutral",
});
</script>

<template>
  <span class="bdg" :class="tone === 'neutral' ? '' : tone"><slot /></span>
</template>
