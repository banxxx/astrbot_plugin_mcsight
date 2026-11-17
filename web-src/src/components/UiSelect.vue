<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import { useAnchoredPopup } from "./useAnchoredPopup";

interface Option { value: string; label: string; description?: string; disabled?: boolean }
const props = withDefaults(defineProps<{
  modelValue: string;
  options: Option[];
  disabled?: boolean;
  placeholder?: string;
  ariaLabel?: string;
  id?: string;
}>(), { disabled: false, placeholder: "请选择", ariaLabel: "选择选项" });
const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
  (e: "change", value: string): void;
}>();
const uid = useId();
const listId = `select-${uid}`;
const open = ref(false);
const active = ref(-1);
const anchor = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const popup = ref<HTMLElement | null>(null);
const selected = computed(() => props.options.find((option) => option.value === props.modelValue));
const { popupStyle, popupTarget } = useAnchoredPopup(open, anchor, popup);
const activeId = computed(() => open.value && active.value >= 0 ? `${listId}-${active.value}` : undefined);

function scrollToActive() {
  void nextTick(() => popup.value?.querySelector<HTMLElement>("[data-active='true']")?.scrollIntoView({ block: "nearest" }));
}
function show() {
  if (props.disabled) return;
  const current = props.options.findIndex((option) => option.value === props.modelValue && !option.disabled);
  active.value = current >= 0 ? current : props.options.findIndex((option) => !option.disabled);
  open.value = true;
  scrollToActive();
}
function toggle() { open.value ? open.value = false : show(); }
function pick(index: number) {
  const option = props.options[index];
  if (props.disabled || !option || option.disabled) return;
  emit("update:modelValue", option.value);
  emit("change", option.value);
  open.value = false;
  trigger.value?.focus();
}
function move(step: number) {
  if (!open.value) { show(); return; }
  if (!props.options.length) return;
  let index = active.value;
  for (let i = 0; i < props.options.length; i++) {
    index = (index + step + props.options.length) % props.options.length;
    if (!props.options[index]?.disabled) { active.value = index; break; }
  }
  scrollToActive();
}
function onKey(event: KeyboardEvent) {
  if (props.disabled) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault(); move(event.key === "ArrowDown" ? 1 : -1);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault(); open.value ? pick(active.value) : show();
  } else if (event.key === "Escape") {
    event.preventDefault(); event.stopPropagation(); open.value = false;
  } else if (event.key === "Tab") {
    open.value = false;
  } else if (open.value && (event.key === "Home" || event.key === "End")) {
    event.preventDefault();
    const available = props.options.map((option, index) => ({ option, index })).filter(({ option }) => !option.disabled);
    active.value = event.key === "Home" ? available[0]?.index ?? -1 : available[available.length - 1]?.index ?? -1;
    scrollToActive();
  } else if (event.key.length === 1 && event.key.trim()) {
    const index = props.options.findIndex((option) => !option.disabled && option.label.toLowerCase().startsWith(event.key.toLowerCase()));
    if (index >= 0) { if (!open.value) show(); active.value = index; scrollToActive(); }
  }
}
watch(() => props.disabled, (disabled) => { if (disabled) open.value = false; });
watch(() => props.options, () => {
  if (active.value >= props.options.length || props.options[active.value]?.disabled) {
    active.value = props.options.findIndex((option) => !option.disabled);
  }
}, { deep: true });
</script>

<template>
  <div ref="anchor" class="ui-select">
    <button ref="trigger" :id="id" class="select-trigger" :class="{ open, placeholder: !selected }" type="button"
      role="combobox" :aria-label="ariaLabel" aria-haspopup="listbox" :aria-controls="listId"
      :aria-expanded="open" :aria-activedescendant="activeId" :disabled="disabled" @click="toggle" @keydown="onKey">
      <span class="selected-label">{{ selected?.label || placeholder }}</span>
      <span class="select-icon"><UiIcon :name="open ? 'chevron-up' : 'chevron-down'" :size="16" /></span>
    </button>
    <Teleport :to="popupTarget">
      <div v-if="open" ref="popup" class="select-menu" :style="popupStyle" :id="listId" role="listbox" :aria-label="ariaLabel">
        <button v-for="(option, index) in options" :id="`${listId}-${index}`" :key="option.value" type="button" role="option"
          tabindex="-1" class="select-option" :class="{ selected: option.value === modelValue, active: index === active }"
          :aria-selected="option.value === modelValue" :disabled="option.disabled" :data-active="index === active"
          @pointerdown.prevent @pointermove="!option.disabled && (active = index)" @click="pick(index)">
          <span class="option-check"><UiIcon v-if="option.value === modelValue" name="check" :size="16" /></span>
          <span class="option-label">{{ option.label }}</span>
          <span v-if="option.description" class="option-description">{{ option.description }}</span>
        </button>
        <div v-if="!options.length" class="select-empty">暂无可选项</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ui-select { min-width: 0; width: 100%; }
.select-trigger { display: flex; align-items: center; gap: 8px; width: 100%; height: var(--h-ctl); padding: 0 10px 0 12px; border: 1px solid var(--ctl-bd); border-radius: 8px; background: var(--surface); color: var(--ink); text-align: left; cursor: pointer; transition: border-color var(--transition), box-shadow var(--transition); }
.selected-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.select-icon { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 28px; height: 24px; border-left: 1px solid #e9ecf3; color: var(--ink3); }
.select-trigger.open, .select-trigger:focus-visible { outline: none; border-color: var(--focus); box-shadow: 0 0 0 1px var(--focus), 0 0 4px var(--focus-ring); }
.select-trigger:disabled { color: var(--ink4); background: var(--disabled-bg); border-color: var(--disabled-bd); cursor: not-allowed; }
.select-trigger.placeholder { color: var(--ink3); }
.select-menu { position: fixed; z-index: 100; overflow-y: auto; overscroll-behavior: contain; padding: 6px; border: 1px solid var(--bd); border-radius: 10px; background: var(--surface); box-shadow: 0 6px 18px #24335312; }
.select-option { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 40px; padding: 9px 10px; border: none; border-radius: 6px; background: none; color: var(--ink); text-align: left; cursor: pointer; }
.option-check { display: inline-flex; flex: none; width: 16px; color: var(--ac); }
.option-label { flex: 1 1 auto; min-width: 0; font-size: 13px; overflow-wrap: anywhere; }
.option-description { flex: none; max-width: 45%; font-size: 12px; color: var(--ink3); overflow-wrap: anywhere; }
.select-option.active:not(.selected), .select-option:hover:not(:disabled) { background: #f8f9ff; }
.select-option.selected { background: var(--ac-soft); color: var(--ac); font-weight: 500; }
.select-option:disabled { color: var(--ink4); cursor: not-allowed; }
.select-empty { padding: 12px; font-size: 12px; color: var(--ink3); }
@media (max-width: 900px), (max-height: 520px) {
  .select-trigger, .select-option { min-height: 44px; }
}
</style>
