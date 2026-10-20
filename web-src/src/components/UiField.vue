<script setup lang="ts">
defineProps<{ label: string; required?: boolean; help?: string; err?: string }>();
</script>

<template>
  <div class="field">
    <label class="mlab">
      {{ label }}<i v-if="required">*</i>
    </label>
    <slot />
    <div v-if="help" class="fhelp">{{ help }}</div>
    <div v-if="err" class="err">{{ err }}</div>
  </div>
</template>
