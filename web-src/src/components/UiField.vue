<script setup lang="ts">
defineProps<{ label: string; required?: boolean; help?: string; err?: string; for?: string }>();
</script>

<template>
  <div class="field">
    <label class="mlab" :for="$props.for">{{ label }}<i v-if="required">*</i><slot name="label-extra" /></label>
    <slot />
    <div v-if="help" class="fhelp">{{ help }}</div>
    <div v-if="err" class="err" role="alert">{{ err }}</div>
  </div>
</template>
