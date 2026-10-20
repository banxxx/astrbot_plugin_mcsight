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
  }>(),
  { rows: 0, autocomplete: "off" },
);

const emit = defineEmits<{
  (e: "update:modelValue", v: string): void;
  (e: "enter"): void;
}>();

function onInput(v: string) {
  emit("update:modelValue", v);
}
</script>

<template>
  <textarea
    v-if="props.rows"
    class="inp"
    :class="{ err: props.error }"
    :id="props.id"
    :rows="props.rows"
    :placeholder="props.placeholder"
    :value="props.modelValue"
    @input="onInput(($event.target as HTMLTextAreaElement).value)"
  />
  <input
    v-else
    class="inp"
    :class="[{ err: props.error }, props.mono === false ? '' : 'mono']"
    :id="props.id"
    :placeholder="props.placeholder"
    :autocomplete="props.autocomplete"
    :value="props.modelValue"
    @input="onInput(($event.target as HTMLInputElement).value)"
    @keyup.enter="emit('enter')"
  />
</template>
