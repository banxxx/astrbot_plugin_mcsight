<script setup lang="ts">
import UiIcon from "./UiIcon.vue";

withDefaults(
  defineProps<{
    kind?: "pri" | "sec" | "tool" | "line" | "danger" | "danger-light" | "ghost";
    size?: "ctl" | "sm";
    disabled?: boolean;
    loading?: boolean;
    iconOnly?: boolean;
    title?: string;
  }>(),
  { kind: "sec", size: "ctl", disabled: false, loading: false, iconOnly: false },
);
</script>

<template>
  <button
    class="btn"
    :class="[`is-${kind}`, { 'btn-sm': size === 'sm', 'icon-only': iconOnly, loading }]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    :title="title"
    type="button"
  >
    <UiIcon v-if="loading" name="loader-circle" :size="16" class="spinner" />
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: var(--h-ctl);
  padding: 0 16px;
  border: 1px solid var(--ctl-bd);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink2);
  box-shadow: 0 1px 2px #25304b08;
  font-family: var(--ff);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition), box-shadow var(--transition);
}
.btn :deep(svg) { flex: none; }
.btn:hover:not(:disabled) { background: #f8f9ff; border-color: #c9d1e0; }
.is-pri { background: var(--ac); border-color: var(--ac); color: #fff; font-weight: 600; box-shadow: 0 2px 4px #4f5ed51a; }
.is-pri:hover:not(:disabled) { background: var(--ac-h); border-color: var(--ac-h); }
.is-pri:active:not(:disabled) { background: #3d4cb7; box-shadow: none; }
.is-tool, .is-line { padding: 0 14px; }
.is-danger, .is-danger-light { padding: 0 14px; background: #fff2f3; border-color: var(--bad-bd); color: var(--bad); font-weight: 600; box-shadow: none; }
.is-danger:hover:not(:disabled), .is-danger-light:hover:not(:disabled) { background: #fce8eb; border-color: #dbaeb5; }
.is-ghost { height: 34px; padding: 0 10px; gap: 6px; background: #f1f3fd; border-color: transparent; border-radius: 7px; color: var(--ac); box-shadow: none; }
.is-ghost:hover:not(:disabled) { background: #e7ebfc; border-color: transparent; }
.btn-sm { height: 32px; padding: 0 10px; gap: 6px; border-radius: 7px; }
.icon-only { width: 36px; height: 36px; padding: 0; border-color: #e9edf4; background: #f4f6fa; box-shadow: none; }
.icon-only.btn-sm { width: 32px; height: 32px; }
.btn:disabled:not(.loading) { background: #f2f4f8; color: var(--ink4); border-color: #e8ecf3; box-shadow: none; cursor: not-allowed; }
.btn.loading { cursor: progress; }
.btn.loading :slotted(svg) { display: none; }
.spinner { animation: turn 900ms linear infinite; }
@keyframes turn { to { transform: rotate(360deg); } }
@media (max-width: 900px), (max-height: 520px) {
  .btn, .btn-sm, .is-ghost { height: 44px; }
  .icon-only, .icon-only.btn-sm { width: 44px; height: 44px; }
}
</style>
