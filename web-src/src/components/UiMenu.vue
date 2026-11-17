<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from "vue";
import UiButton from "./UiButton.vue";
import UiIcon from "./UiIcon.vue";
import { useAnchoredPopup } from "./useAnchoredPopup";

interface MenuItem { value: string; label: string; icon?: string; danger?: boolean; disabled?: boolean }
const props = withDefaults(defineProps<{
  items: MenuItem[];
  label?: string;
  icon?: string;
  align?: "start" | "end";
  disabled?: boolean;
}>(), { icon: "more-horizontal", align: "end", disabled: false });
const emit = defineEmits<{ (e: "select", value: string): void }>();
const menuId = `menu-${useId()}`;
const open = ref(false);
const anchor = ref<HTMLElement | null>(null);
const popup = ref<HTMLElement | null>(null);
const active = ref(-1);
const { popupStyle, popupTarget, reposition } = useAnchoredPopup(open, anchor, popup, { minWidth: 200 });
const alignedStyle = computed(() => {
  if (props.align !== "end" || !anchor.value) return popupStyle.value;
  const rect = anchor.value.getBoundingClientRect();
  const width = Number.parseFloat(popupStyle.value.width || "200");
  return { ...popupStyle.value, left: `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px` };
});
const attrs = computed(() => ({ "aria-haspopup": "menu" as const, "aria-expanded": open.value, "aria-controls": menuId, disabled: props.disabled }));
function focusItem() {
  void nextTick(() => popup.value?.querySelectorAll<HTMLButtonElement>(".menu-item")[active.value]?.focus());
}
function restoreFocus() { anchor.value?.querySelector<HTMLButtonElement>("button")?.focus(); }
function toggle() {
  if (props.disabled) return;
  open.value = !open.value;
  if (open.value) {
    active.value = props.items.findIndex((item) => !item.disabled);
    void nextTick(() => { reposition(); focusItem(); });
  }
}
function select(item: MenuItem) {
  if (props.disabled || item.disabled) return;
  open.value = false;
  restoreFocus();
  emit("select", item.value);
}
function move(step: number) {
  if (!props.items.length) return;
  let index = active.value;
  for (let i = 0; i < props.items.length; i++) {
    index = (index + step + props.items.length) % props.items.length;
    if (!props.items[index]?.disabled) { active.value = index; break; }
  }
  focusItem();
}
function keydown(event: KeyboardEvent) {
  if (props.disabled) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault(); open.value ? move(event.key === "ArrowDown" ? 1 : -1) : toggle();
  } else if (event.key === "Escape") {
    event.preventDefault(); event.stopPropagation(); open.value = false; restoreFocus();
  } else if (event.key === "Tab") {
    open.value = false; restoreFocus();
  } else if (open.value && (event.key === "Home" || event.key === "End")) {
    event.preventDefault();
    const available = props.items.map((item, index) => ({ item, index })).filter(({ item }) => !item.disabled);
    active.value = event.key === "Home" ? available[0]?.index ?? -1 : available[available.length - 1]?.index ?? -1;
    focusItem();
  }
}
watch(() => props.disabled, (disabled) => { if (disabled) open.value = false; });
</script>

<template>
  <div ref="anchor" class="ui-menu" @keydown="keydown">
    <slot name="trigger" :open="open" :toggle="toggle" :attrs="attrs">
      <UiButton v-bind="attrs" :icon-only="!label" :title="label || '更多操作'" @click="toggle">
        <UiIcon :name="icon" :size="16" /><span v-if="label">{{ label }}</span>
      </UiButton>
    </slot>
    <Teleport :to="popupTarget">
      <div v-if="open" :id="menuId" ref="popup" class="menu-panel" :style="alignedStyle" role="menu" :aria-label="label || '更多操作'" @keydown="keydown">
        <button v-for="(item, index) in items" :key="item.value" type="button" role="menuitem"
          class="menu-item" :class="{ danger: item.danger }" :disabled="item.disabled" tabindex="-1"
          @focus="active = index" @click="select(item)">
          <UiIcon v-if="item.icon" :name="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ui-menu { display: inline-flex; position: relative; flex: none; }
.menu-panel { position: fixed; z-index: 100; overflow-y: auto; overscroll-behavior: contain; padding: 6px; border: 1px solid var(--bd); border-radius: 10px; background: var(--surface); box-shadow: 0 6px 18px #24335312; }
.menu-item { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 40px; padding: 9px 10px; border: none; border-radius: 6px; background: none; color: var(--ink); text-align: left; font-size: 13px; cursor: pointer; }
.menu-item svg { flex: none; color: var(--ink3); }
.menu-item span { overflow-wrap: anywhere; }
.menu-item:hover:not(:disabled), .menu-item:focus-visible { outline: none; background: #f0f2ff; color: var(--ac); }
.menu-item.danger, .menu-item.danger svg { color: var(--bad); }
.menu-item.danger:hover:not(:disabled) { background: var(--bad-bg); }
.menu-item:disabled, .menu-item:disabled svg { color: var(--ink4); cursor: not-allowed; }
@media (max-width: 900px), (max-height: 520px) { .menu-item { min-height: 44px; } }
</style>
