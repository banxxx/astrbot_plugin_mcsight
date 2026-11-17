<script setup lang="ts">
defineProps<{ on?: boolean; disabled?: boolean; label?: string }>();
const emit = defineEmits<{ (e: "toggle"): void }>();
</script>

<template>
  <button
    class="sw"
    :class="{ on }"
    :disabled="disabled"
    type="button"
    role="switch"
    :aria-checked="!!on"
    :aria-label="label"
    @click="emit('toggle')"
  ><i aria-hidden="true" /></button>
</template>
