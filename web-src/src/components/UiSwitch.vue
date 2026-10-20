<script setup lang="ts">
defineProps<{ on?: boolean; disabled?: boolean }>();
const emit = defineEmits<{ (e: "toggle"): void }>();
</script>

<template>
  <button
    class="sw"
    :class="{ on }"
    :disabled="disabled"
    type="button"
    role="switch"
    :aria-checked="on"
    @click="emit('toggle')"
  >
    <i />
  </button>
</template>

<style scoped>
.sw:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
