<script setup lang="ts">
withDefaults(defineProps<{ tone?: "neutral" | "off" | "ok" | "warn" | "port" | "bad" }>(), { tone: "neutral" });
</script>

<template>
  <span class="bdg" :class="tone === 'neutral' ? '' : tone"><slot /></span>
</template>
