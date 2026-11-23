<script setup lang="ts">
import { computed, ref } from "vue";
import UiIcon from "./UiIcon.vue";
const props = withDefaults(
  defineProps<{
    modelValue: string;
    hasValue?: boolean;
    placeholder?: string;
    error?: boolean;
    id?: string;
    clearable?: boolean;
    clearTitle?: string;
    disabled?: boolean;
  }>(),
  { hasValue: false, placeholder: "", clearable: false, clearTitle: "清除已存的值", disabled: false },
);
const emit = defineEmits<{ (e: "update:modelValue", v: string): void; (e: "clear"): void }>();
const revealed = ref(false);
const showClear = computed(() => props.clearable && props.hasValue && !props.modelValue);
</script>

<template>
  <div class="sec" :class="{ err: props.error, disabled: props.disabled }">
    <input
      class="v"
      :id="props.id"
      :type="revealed ? 'text' : 'password'"
      :value="props.modelValue"
      :placeholder="props.placeholder || (props.hasValue ? '留空保留原值' : '请输入')"
      :disabled="props.disabled"
      :aria-invalid="props.error || undefined"
      autocomplete="new-password"
      spellcheck="false"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <button v-if="showClear" class="del" type="button" :title="props.clearTitle" :aria-label="props.clearTitle" :disabled="props.disabled" @click="emit('clear')">
      <UiIcon name="trash" :size="16" />
    </button>
    <button class="eye" type="button" :title="revealed ? '隐藏本次输入' : '显示本次输入'" :aria-label="revealed ? '隐藏本次输入' : '显示本次输入'" :aria-pressed="revealed" :disabled="props.disabled" @click="revealed = !revealed">
      <UiIcon :name="revealed ? 'eye' : 'eye-off'" :size="16" />
    </button>
  </div>
</template>

<style scoped>
.sec { display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; height: var(--h-ctl); padding: 0 10px 0 12px; border: 1px solid var(--ctl-bd); border-radius: 8px; background: var(--surface); transition: border-color var(--transition), box-shadow var(--transition); }
.sec:focus-within { border-color: var(--focus); box-shadow: 0 0 0 1px var(--focus), 0 0 4px var(--focus-ring); position: relative; z-index: 1; }
.sec.err { border-color: #d78b93; background: #fffafb; }
.sec.disabled { border-color: var(--disabled-bd); background: var(--disabled-bg); }
.v { flex: 1 1 auto; min-width: 0; width: 0; border: none; outline: none; padding: 0; background: none; color: var(--ink); font-family: var(--ff); font-size: 13px; line-height: 1.5; }
.v::placeholder { color: var(--ink3); opacity: 1; }
.v:disabled { color: var(--ink4); cursor: not-allowed; }
.eye, .del { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 28px; height: 24px; padding: 0; border: none; border-left: 1px solid #e9ecf3; border-radius: 0; background: none; color: var(--ink3); cursor: pointer; }
.eye:hover:not(:disabled) { color: var(--ac); }
.del:hover:not(:disabled) { color: var(--bad); }
.eye:disabled, .del:disabled { color: var(--ink4); cursor: not-allowed; }
@media (max-width: 900px), (max-height: 520px) {
  .sec { height: 44px; }
}
</style>
