<script setup lang="ts">
import { computed } from "vue";
import UiIcon from "./UiIcon.vue";
const props = withDefaults(defineProps<{ tone?: "info" | "warn" | "bad" | "ok" | "neutral"; lock?: boolean }>(), { tone: "info" });
const iconName = computed(() => props.lock ? "lock" : props.tone === "bad" ? "circle-alert" : props.tone === "ok" ? "circle-check" : "info");
</script>

<template>
  <div class="notice" :class="tone" :role="tone === 'bad' ? 'alert' : undefined">
    <UiIcon :name="iconName" :size="17" />
    <div><slot /></div>
  </div>
</template>
