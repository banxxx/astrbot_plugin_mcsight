<script setup lang="ts">
import { ref } from "vue";
import UiIcon from "./UiIcon.vue";

/* 密钥行：已存的值内核从不下发（view/config 只给 has_value），
   所以眼睛只影响"本次输入的内容"是否明文，不会回显历史密钥。 */
const props = withDefaults(
  defineProps<{ modelValue: string; hasValue?: boolean; placeholder?: string; error?: boolean; id?: string }>(),
  { hasValue: false, placeholder: "" },
);

const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();
const revealed = ref(false);
</script>

<template>
  <div class="sec" :class="{ err: props.error, set: props.hasValue && !props.modelValue }">
    <UiIcon name="key" :size="12" class="k" />
    <input
      class="v"
      :id="props.id"
      :type="revealed ? 'text' : 'password'"
      :value="props.modelValue"
      :placeholder="props.placeholder || (props.hasValue ? '••••••••••••' : '未设置')"
      autocomplete="new-password"
      spellcheck="false"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <button
      class="eye"
      type="button"
      :title="revealed ? '掩码' : '明文显示本次输入'"
      @click="revealed = !revealed"
    >
      <UiIcon :name="revealed ? 'eye-off' : 'eye'" :size="13" />
    </button>
  </div>
</template>

<style scoped>
.sec {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
}

.sec:focus-within {
  border-color: var(--ac);
  background: var(--surface);
}

.sec.err {
  border-color: var(--bad);
}

.k {
  flex: none;
  color: var(--ink3);
}

.v {
  flex: 1 1 auto;
  min-width: 0;
  border: none;
  outline: none;
  background: none;
  color: var(--ink);
  font-family: var(--fm);
  font-size: 13px;
  letter-spacing: 0;
}

.v::placeholder {
  color: var(--ink4);
}

.eye {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--ink3);
  cursor: pointer;
}

.eye:hover {
  color: var(--ac);
}
</style>
