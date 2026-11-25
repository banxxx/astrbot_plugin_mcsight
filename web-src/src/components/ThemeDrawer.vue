<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import type { Skin } from "../store";

const props = defineProps<{ open: boolean; skin: Skin; saving: boolean }>();
const emit = defineEmits<{
  (e: "preview", skin: Skin): void;
  (e: "cancel"): void;
  (e: "apply", skin: Skin): void;
}>();

const OPTIONS: {
  value: Skin;
  title: string;
  desc: string;
  tags: string[];
}[] = [
  {
    value: "classic",
    title: "经典",
    desc: "保持现有外观：蓝色强调、紧凑表格与线性图标。",
    tags: ["侧栏导航", "表格列表", "线性图标"],
  },
  {
    value: "cream",
    title: "奶油",
    desc: "奶油底色、蓝紫强调、顶部导航与圆润填充图标。",
    tags: ["顶栏导航", "卡片列表", "圆润图标"],
  },
];

const draft = ref<Skin>(props.skin);
const panel = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;

function select(value: Skin) {
  draft.value = value;
  emit("preview", value);
}

function cancel() {
  emit("cancel");
}

function apply() {
  if (props.saving) return;
  emit("apply", draft.value);
}

function focusables(): HTMLElement[] {
  const root = panel.value;
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>("button:not(:disabled), [tabindex='0']"))
    .filter(node => node.getClientRects().length);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    cancel();
    return;
  }
  if (event.key !== "Tab") return;
  const items = focusables();
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function lock() {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  document.documentElement.classList.add("theme-open");
  document.addEventListener("keydown", onKeydown, true);
  void nextTick(() => panel.value?.focus());
}

function unlock() {
  document.documentElement.classList.remove("theme-open");
  document.removeEventListener("keydown", onKeydown, true);
  previousFocus?.focus();
  previousFocus = null;
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      draft.value = props.skin;
      lock();
    } else {
      unlock();
    }
  },
  { immediate: true },
);

watch(
  () => props.skin,
  (skin) => {
    if (props.open) draft.value = skin;
  },
);

onBeforeUnmount(unlock);
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="theme-layer">
      <button class="theme-veil" type="button" aria-label="关闭主题样式" @click="cancel" />
      <aside ref="panel" class="theme-drawer" role="dialog" aria-modal="true" aria-labelledby="theme-title" tabindex="-1">
        <header class="theme-head">
          <span class="theme-head-icon"><UiIcon name="palette" :size="20" /></span>
          <div class="theme-head-copy">
            <h2 id="theme-title">主题样式</h2>
            <p>选中即预览；应用后保存。</p>
          </div>
          <button class="theme-close" type="button" aria-label="关闭主题样式" @click="cancel">
            <UiIcon name="x" :size="18" />
          </button>
        </header>

        <div class="theme-divider" />
        <div class="theme-meta"><span>外观预设</span><span>2 套 · 即时预览</span></div>

        <div class="theme-list" role="radiogroup" aria-label="外观预设">
          <button v-for="option in OPTIONS" :key="option.value" class="theme-card"
            :class="{ selected: draft === option.value }" type="button" role="radio"
            :aria-checked="draft === option.value" @click="select(option.value)">
            <div class="theme-preview" :class="option.value" aria-hidden="true">
              <template v-if="option.value === 'classic'">
                <div class="mini-side"><i /><i /><i /><i /></div>
                <div class="mini-main">
                  <div class="mini-bar" />
                  <div class="mini-summary"><i /><i /><i /></div>
                  <div class="mini-table"><i /><i /><i /></div>
                </div>
              </template>
              <template v-else>
                <div class="mini-top"><i /><span /><span /><b /></div>
                <div class="mini-cream-main">
                  <div class="mini-title" />
                  <div class="mini-cards"><i /><i /></div>
                </div>
              </template>
            </div>

            <div class="theme-card-body">
              <div class="theme-card-title">
                <b>{{ option.title }}</b>
                <span v-if="skin === option.value" class="theme-state current">当前使用</span>
                <span v-else class="theme-state">可切换</span>
              </div>
              <div class="theme-tags"><span v-for="tag in option.tags" :key="tag">{{ tag }}</span></div>
              <p>{{ option.desc }}</p>
            </div>
            <span class="theme-radio" :class="{ checked: draft === option.value }">
              <UiIcon v-if="draft === option.value" name="check" :size="13" />
            </span>
          </button>
        </div>

        <div class="theme-hint">
          <UiIcon name="info" :size="15" />
          <span>切换只影响外观，当前页面未保存的更改不会丢失。</span>
        </div>

        <footer class="theme-foot">
          <button class="theme-reset" type="button" @click="select('classic')">恢复默认</button>
          <span class="theme-spacer" />
          <button class="theme-btn" type="button" :disabled="saving" @click="cancel">取消</button>
          <button class="theme-btn primary" type="button" :disabled="saving" @click="apply">
            <UiIcon :name="saving ? 'loader-circle' : 'check'" :size="16" :class="{ spinning: saving }" />
            {{ saving ? "保存中…" : "应用并保存" }}
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.theme-layer { position: fixed; inset: 0; z-index: 75; display: flex; justify-content: flex-end; }
.theme-veil { position: absolute; inset: 0; border: 0; padding: 0; background: var(--veil); cursor: default; }
.theme-drawer {
  position: relative;
  display: flex;
  width: min(452px, calc(100vw - 24px));
  height: 100%;
  min-height: 0;
  flex-direction: column;
  gap: 14px;
  padding: 24px;
  border: 0;
  border-left: 1px solid var(--bd);
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--drawer-shadow, -18px 0 40px rgba(35, 46, 74, 0.16));
  outline: none;
}
.theme-head { display: flex; align-items: center; gap: 12px; }
.theme-head-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--r-ctl);
  background: var(--ac-soft);
  color: var(--ac);
}
.theme-head-copy { display: flex; min-width: 0; flex: 1 1 auto; flex-direction: column; gap: 4px; }
.theme-head-copy h2 { margin: 0; color: var(--ink); font-size: 17px; font-weight: 600; line-height: 1.5; }
.theme-head-copy p { margin: 0; color: var(--ink3); font-size: 12px; line-height: 1.5; }
.theme-close {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink3);
  cursor: pointer;
}
.theme-close:hover { border-color: var(--ac-line); background: var(--ac-soft); color: var(--ac); }
.theme-divider { height: 1px; background: var(--row); }
.theme-meta { display: flex; align-items: center; gap: 12px; color: var(--ink3); font-size: 12px; }
.theme-meta > span:first-child { font-weight: 600; }
.theme-meta > span:last-child { margin-left: auto; font-size: 11px; }
/* 矮屏或横屏时抽屉总高有上限，选项区自己滚动，底部按钮始终可见。 */
.theme-list { display: flex; min-height: 0; flex-direction: column; gap: 12px; overflow-y: auto; }
.theme-card {
  position: relative;
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--bd);
  border-radius: var(--r-card);
  background: var(--thead);
  color: var(--ink);
  font-family: var(--ff);
  text-align: left;
  cursor: pointer;
  transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
}
.theme-card:hover { border-color: var(--ac-line); }
.theme-card.selected { border-color: var(--ac); box-shadow: 0 0 0 1px var(--ac), 0 8px 20px rgba(79, 94, 213, 0.1); }
.theme-preview { position: relative; height: 96px; overflow: hidden; border-radius: calc(var(--r-card) - 4px); }
.theme-preview.classic { display: flex; background: #f4f6fa; }
.theme-preview.cream { background: linear-gradient(145deg, #fcf9f7 0%, #f3ebfa 36%, #fbf0f4 70%, #f1f6f1 100%); }
.mini-side { display: flex; width: 26%; flex-direction: column; gap: 6px; padding: 10px 7px; background: #fff; border-right: 1px solid #e8ecf3; }
.mini-side i { display: block; height: 4px; border-radius: 2px; background: #dfe5ef; }
.mini-side i:first-child { width: 58%; margin-bottom: 4px; background: #4f5ed5; }
.mini-side i:nth-child(2) { width: 82%; background: #c9d3ef; }
.mini-main { display: flex; min-width: 0; flex: 1 1 auto; flex-direction: column; gap: 6px; padding: 10px; }
.mini-bar { width: 42%; height: 6px; border-radius: 3px; background: #c4cbe0; }
.mini-summary { display: flex; gap: 4px; }
.mini-summary i { display: block; width: 20px; height: 11px; border: 1px solid #dce2ec; border-radius: 3px; background: #fff; }
.mini-table { display: flex; flex: 1 1 auto; flex-direction: column; justify-content: center; gap: 5px; border: 1px solid #dce2ec; border-radius: 4px; background: #fff; padding: 5px 7px; }
.mini-table i { display: block; height: 5px; border-radius: 2px; background: #e4e9f2; }
.mini-table i:first-child { width: 72%; }
.mini-top { display: flex; align-items: center; gap: 4px; height: 22px; padding: 0 8px; background: #fffdfb; border-bottom: 1px solid #e7def0; }
.mini-top i { display: block; width: 8px; height: 8px; border-radius: 3px; background: #f6d9cb; }
.mini-top span { display: block; width: 22px; height: 6px; border-radius: 3px; background: #eee7f6; }
.mini-top span:nth-of-type(2) { width: 34px; background: #d9cef3; }
.mini-top b { display: block; width: 18px; height: 6px; margin-left: auto; border-radius: 3px; background: #f1ecfb; }
.mini-cream-main { display: flex; flex-direction: column; gap: 6px; padding: 8px; }
.mini-title { width: 38%; height: 6px; border-radius: 3px; background: #b8a9d9; }
.mini-cards { display: flex; gap: 6px; }
.mini-cards i { display: block; width: 50%; height: 44px; border: 1px solid #fff; border-radius: 6px; background: #fffdfb; box-shadow: 0 3px 7px #5b3e7a14; }
.theme-card-body { display: flex; flex-direction: column; gap: 6px; }
.theme-card-title { display: flex; align-items: center; gap: 10px; }
.theme-card-title b { color: var(--ink); font-size: 13px; font-weight: 600; }
.theme-state { color: var(--ink3); font-size: 10px; font-weight: 600; }
.theme-state.current { color: var(--ac); }
.theme-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.theme-tags span { padding: 2px 7px; border-radius: 999px; background: var(--surface); color: var(--ink3); font-size: 10px; line-height: 1.5; }
.theme-card-body p { margin: 0; color: var(--ink3); font-size: 11px; line-height: 1.5; }
.theme-radio {
  position: absolute;
  right: 14px;
  bottom: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--ctl-bd);
  border-radius: 50%;
  background: var(--surface);
  color: #fff;
}
.theme-radio.checked { border-color: var(--ac); background: var(--ac); }
.theme-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 8px 11px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink3);
  font-size: 11px;
  line-height: 1.5;
}
.theme-hint svg { flex: none; }
.theme-foot { display: flex; align-items: center; gap: 8px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--row); }
.theme-spacer { flex: 1 1 auto; }
.theme-reset {
  min-height: 32px;
  padding: 0 6px;
  border: 0;
  background: transparent;
  color: var(--ink3);
  font-family: var(--ff);
  font-size: 12px;
  cursor: pointer;
}
.theme-reset:hover { color: var(--ac); }
.theme-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  border: 1px solid var(--ctl-bd);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--ink2);
  font-family: var(--ff);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.theme-btn:hover:not(:disabled) { border-color: var(--ac-line); background: var(--ac-soft); color: var(--ac); }
.theme-btn.primary { padding: 0 18px; border-color: var(--ac); background: var(--ac); color: #fff; font-weight: 600; }
.theme-btn.primary:hover:not(:disabled) { border-color: var(--ac-h); background: var(--ac-h); color: #fff; }
.theme-btn:disabled { opacity: 0.62; cursor: not-allowed; }
.spinning { animation: theme-spin 900ms linear infinite; }
@keyframes theme-spin { to { transform: rotate(360deg); } }

@media (max-width: 900px), (max-height: 520px) {
  .theme-layer { align-items: flex-end; }
  .theme-drawer {
    width: 100%;
    height: auto;
    max-height: min(86vh, 720px);
    max-height: min(86dvh, 720px);
    padding: 20px 16px calc(16px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--bd);
    border-left: 0;
    border-radius: 20px 20px 0 0;
    box-shadow: 0 -18px 40px rgba(35, 46, 74, 0.2);
  }
  .theme-preview { height: 82px; }
  .theme-btn, .theme-reset { height: 44px; }
  .theme-close { width: 40px; height: 40px; }
}
@media (prefers-reduced-motion: reduce) {
  .theme-btn, .theme-card { transition: none; }
  .spinning { animation: none; }
}
</style>
