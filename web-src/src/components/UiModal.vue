<script setup lang="ts">
/* 弹窗规矩：遮罩点击与 Esc 都不关闭，只走按钮；同档位内尺寸恒定，
   高度按该档位最重状态定高；窄屏允许宽度收缩（max-width），高度不变。 */
withDefaults(
  defineProps<{
    cls?: "m-confirm" | "m-single" | "m-form" | "m-list" | "m-json";
    title: string;
    sub?: string;
    subMono?: boolean;
    info?: string;
  }>(),
  { cls: "m-form", subMono: false },
);
</script>

<template>
  <div class="veil">
    <div class="modal" :class="cls" role="dialog" :aria-label="title">
      <div class="mh">
        <h3 :class="subMono ? 'mono' : ''">{{ title }}</h3>
        <div v-if="sub" class="ms" :class="subMono ? 'mono' : ''">{{ sub }}</div>
      </div>
      <div class="mb"><slot /></div>
      <div class="mf">
        <span v-if="info" class="info">{{ info }}</span>
        <span v-else class="spacer" />
        <slot name="foot" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.veil {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--veil);
}

.modal {
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--bd);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: 0 18px 44px rgba(30, 37, 50, 0.16);
}

.m-confirm {
  width: 440px;
  height: 260px;
}

.m-single {
  width: 520px;
  height: 410px;
}

.m-form,
.m-list {
  width: 520px;
  height: 500px;
}

.m-json {
  width: 760px;
  height: 500px;
}

.mh {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 5px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--row);
}

.mh h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.ms {
  font-size: 11px;
  color: var(--ac);
}

.mb {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  padding: 16px 20px;
  overflow-y: auto;
}

.mf {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 20px;
  border-top: 1px solid var(--row);
}

.mf .info {
  margin-right: auto;
  font-size: 11px;
  color: var(--ink3);
}

.mf .spacer {
  flex: 1 1 auto;
}

.x {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--ink4);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.x:hover {
  color: var(--bad);
}

@media (max-width: 560px) {
  .modal {
    width: calc(100% - 24px);
  }
}
</style>
