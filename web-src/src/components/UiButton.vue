<script setup lang="ts">
withDefaults(
  defineProps<{
    kind?: "pri" | "sec" | "danger" | "danger-light" | "ghost";
    size?: "ctl" | "sm";
    disabled?: boolean;
    title?: string;
  }>(),
  { kind: "sec", size: "ctl", disabled: false },
);
</script>

<template>
  <button
    class="btn"
    :class="[`is-${kind}`, size === 'sm' ? 'btn-sm' : '']"
    :disabled="disabled"
    :title="title"
    type="button"
  >
    <slot />
  </button>
</template>

<style scoped>
/* 控件高 32 / 圆角 6，四种态取自画布「按钮 4 态」 */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--h-ctl);
  padding: 0 14px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--ink2);
  font-family: var(--ff);
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.btn:disabled {
  background: var(--thead);
  color: var(--ink4);
  border-color: var(--bd);
  cursor: not-allowed;
}

.is-pri {
  background: var(--ac);
  border-color: var(--ac);
  color: #fff;
}

.is-pri:not(:disabled):hover {
  background: var(--ac-h);
  border-color: var(--ac-h);
}

.is-danger {
  background: var(--bad);
  border-color: var(--bad);
  color: #fff;
}

.is-danger-light {
  background: var(--bad-bg);
  border-color: var(--bad-bd);
  color: var(--bad);
}

.is-ghost {
  background: transparent;
  border-color: var(--ac-line);
  color: var(--ac);
}

.btn-sm {
  height: 24px;
  padding: 0 9px;
  gap: 5px;
  font-size: 11px;
  font-weight: 500;
  border-radius: var(--r-badge);
}
</style>
