<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    modelValue: string;
    placeholder?: string;
    error?: boolean;
    mono?: boolean;
    rows?: number;
    id?: string;
    autocomplete?: string;
    disabled?: boolean;
    maxlength?: number;
    inputmode?: "none" | "text" | "tel" | "url" | "email" | "numeric" | "decimal" | "search";
  }>(),
  { rows: 0, autocomplete: "off", disabled: false },
);
const emit = defineEmits<{
  (e: "update:modelValue", v: string): void;
  (e: "enter"): void;
}>();
</script>

<template>
  <textarea
    v-if="props.rows"
    class="inp"
    :class="[{ err: props.error }, props.mono ? 'mono' : '']"
    :id="props.id"
    :rows="props.rows"
    :placeholder="props.placeholder"
    :value="props.modelValue"
    :disabled="props.disabled"
    :maxlength="props.maxlength"
    :aria-invalid="props.error || undefined"
    @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  />
  <input
    v-else
    class="inp"
    :class="[{ err: props.error }, props.mono ? 'mono' : '']"
    :id="props.id"
    :placeholder="props.placeholder"
    :autocomplete="props.autocomplete"
    :value="props.modelValue"
    :disabled="props.disabled"
    :maxlength="props.maxlength"
    :inputmode="props.inputmode"
    :aria-invalid="props.error || undefined"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    @keyup.enter="emit('enter')"
  />
</template>
