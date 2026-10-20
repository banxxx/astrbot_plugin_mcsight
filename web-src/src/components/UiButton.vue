<script setup lang="ts">
withDefaults(
  defineProps<{
    kind?: "pri" | "sec" | "tool" | "line" | "danger" | "danger-light" | "ghost";
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
/* 画布上按钮分五种角色，内边距与字重各不相同，逐一对应：
   sec 弹窗页脚（14/normal）｜tool 卡头工具（12/normal）｜line 带图标的次操作（12/600）
   ghost 强调描边（14/600）｜pri 主操作（14/600）｜danger-light 行内危险（12/600） */
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
  font-weight: 400;
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
  font-weight: 600;
}

.is-pri:not(:disabled):hover {
  background: var(--ac-h);
  border-color: var(--ac-h);
}

.is-danger {
  background: var(--bad);
  border-color: var(--bad);
  color: #fff;
  font-weight: 600;
}

.is-tool {
  padding: 0 12px;
}

.is-line {
  padding: 0 12px;
  font-weight: 600;
}

.is-danger-light {
  padding: 0 12px;
  background: var(--bad-bg);
  border-color: var(--bad-bd);
  color: var(--bad);
  font-weight: 600;
}

.is-ghost {
  border-color: var(--ac-line);
  color: var(--ac);
  font-weight: 600;
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
