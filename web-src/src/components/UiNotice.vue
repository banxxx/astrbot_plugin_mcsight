<script setup lang="ts">
import UiIcon from "./UiIcon.vue";
import { computed } from "vue";

const props = withDefaults(defineProps<{ tone?: "info" | "warn" | "bad" | "ok"; lock?: boolean }>(), {
  tone: "info",
});

const iconName = computed(() =>
  props.tone === "warn" ? "alert" : props.tone === "bad" ? "alert" : props.lock ? "lock" : props.tone === "ok" ? "check" : "info",
);
</script>

<template>
  <div class="notice" :class="tone">
    <UiIcon :name="iconName" :size="14" />
    <div><slot /></div>
  </div>
</template>
