<script lang="ts">
let lockDepth = 0;
let lockedScrollY = 0;
let previousBodyStyle = "";
const modalStack: HTMLElement[] = [];

function lockPageScroll() {
  if (lockDepth++ > 0) return;
  lockedScrollY = window.scrollY || document.documentElement.scrollTop || 0;
  previousBodyStyle = document.body.getAttribute("style") || "";
  const scrollbar = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
  document.body.style.position = "fixed";
  document.body.style.top = `-${lockedScrollY}px`;
  document.body.style.left = "0";
  document.body.style.right = "0";
  document.body.style.width = "100%";
  if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;
  document.documentElement.classList.add("modal-open");
}
function unlockPageScroll() {
  if (lockDepth === 0 || --lockDepth > 0) return;
  if (previousBodyStyle) document.body.setAttribute("style", previousBodyStyle);
  else document.body.removeAttribute("style");
  document.documentElement.classList.remove("modal-open");
  window.scrollTo(0, lockedScrollY);
}
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from "vue";
import UiIcon from "./UiIcon.vue";
import { useIsMobile } from "../lib/useMediaQuery";

const props = withDefaults(defineProps<{
  cls?: "m-confirm" | "m-confirm-typed" | "m-form" | "m-list" | "m-json" | "m-help";
  title: string;
  titleMono?: boolean;
  sub?: string;
  subtitle?: string;
  subMono?: boolean;
  subNote?: boolean;
  info?: string;
}>(), { cls: "m-form", titleMono: false, subMono: false, subNote: false });
const emit = defineEmits<{ (e: "close"): void }>();
const isMobile = useIsMobile();
const subtitleText = computed(() => props.subtitle || props.sub);
const modal = ref<HTMLElement | null>(null);
const titleId = `modal-title-${useId()}`;
const descriptionId = `${titleId}-description`;
let previousFocus: HTMLElement | null = null;
function trapFocus(event: KeyboardEvent) {
  const element = modal.value;
  if (!element || modalStack[modalStack.length - 1] !== element || event.key !== "Tab" || event.defaultPrevented) return;
  // Only the topmost dialog owns its keyboard cycle. Nested popup handlers
  // restore their trigger before this bubbling listener runs.
  if (!element.contains(document.activeElement)) return;
  const focusables = Array.from(element.querySelectorAll<HTMLElement>('button, input, textarea, select, a[href], [tabindex]'))
    .filter((node) => node.tabIndex >= 0 && !node.matches(":disabled") && node.getClientRects().length && !node.closest('[inert], [aria-hidden="true"]'));
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (!first) { event.preventDefault(); element.focus(); return; }
  if (document.activeElement === element) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
    return;
  }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
onMounted(() => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  lockPageScroll();
  if (modal.value) modalStack.push(modal.value);
  document.addEventListener("keydown", trapFocus);
  void nextTick(() => modal.value?.focus());
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", trapFocus);
  if (modal.value) {
    const index = modalStack.indexOf(modal.value);
    if (index >= 0) modalStack.splice(index, 1);
  }
  unlockPageScroll();
  previousFocus?.focus();
});
</script>

<template>
  <div class="veil" :class="{ 'is-mobile': isMobile }">
    <div ref="modal" class="modal" :class="[cls, { 'is-mobile': isMobile }]" role="dialog" aria-modal="true"
      :aria-labelledby="titleId" :aria-describedby="subtitleText ? descriptionId : undefined" tabindex="-1">
      <div class="mh">
        <div class="modal-heading">
          <h3 :id="titleId" :class="titleMono ? 'mono' : ''">{{ title }}</h3>
          <div v-if="subtitleText" :id="descriptionId" class="ms" :class="[subMono ? 'mono' : '', subNote ? 'note' : '']">{{ subtitleText }}</div>
        </div>
        <button class="mx" type="button" aria-label="关闭弹窗" @click="emit('close')"><UiIcon name="x" :size="18" /></button>
      </div>
      <div class="mb"><slot /></div>
      <div class="mf">
        <span v-if="info" class="info">{{ info }}</span>
        <span v-else class="spacer" />
        <slot name="footer"><slot name="foot" /></slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.veil { position: fixed; inset: 0; z-index: 70; display: flex; align-items: center; justify-content: center; padding: 24px; background: var(--veil); }
.modal { display: flex; flex-direction: column; width: 660px; max-width: 100%; max-height: calc(100vh - 48px); max-height: calc(100dvh - 48px); padding: 28px; gap: 20px; border: 1px solid #e1e6ef; border-radius: 14px; outline: none; background: var(--surface); box-shadow: 0 8px 26px #2735550a; }
.mh { display: flex; flex: none; align-items: center; gap: 16px; }
.modal-heading { display: flex; flex: 1 1 auto; min-width: 0; flex-direction: column; gap: 6px; }
.mh h3 { margin: 0; font-size: 20px; line-height: 1.5; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
.ms { font-size: 13px; line-height: 1.5; color: var(--ink3); overflow-wrap: anywhere; }
.mx { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 32px; height: 32px; padding: 0; border: 1px solid #f6f8fc; border-radius: 8px; background: #f6f8fc; color: var(--ink3); cursor: pointer; }
.mx:hover { background: #eef0fd; color: var(--ac); }
.mb { display: flex; flex: 0 1 auto; min-height: 0; flex-direction: column; gap: 20px; overflow-y: auto; overscroll-behavior: contain; }
.mf { display: flex; flex: none; align-items: center; justify-content: flex-end; gap: 8px; padding-top: 16px; border-top: 1px solid #e8ecf3; }
.mf .info { margin-right: auto; min-width: 0; color: var(--ink3); font-size: 12px; overflow-wrap: anywhere; }
.mf .spacer { flex: 1 1 auto; }
@media (max-width: 900px), (max-height: 520px) {
  .veil.is-mobile { padding: 0; align-items: stretch; }
  .modal.is-mobile { width: 100%; height: 100vh; height: 100dvh; max-height: 100vh; max-height: 100dvh; padding: 0; gap: 0; border: none; border-radius: 0; }
  .modal.is-mobile .mh { min-height: 88px; padding: calc(20px + env(safe-area-inset-top)) 20px 20px; border-bottom: 1px solid var(--bd); }
  .modal.is-mobile .mx { width: 44px; height: 44px; }
  .modal.is-mobile .mb { flex: 1 1 auto; padding: 20px; gap: 20px; }
  .modal.is-mobile .mf { min-height: 76px; padding: 16px 20px calc(16px + env(safe-area-inset-bottom)); flex-wrap: wrap; }
  .modal.is-mobile .mf .info { flex-basis: 100%; }
  .modal.is-mobile.m-confirm, .modal.is-mobile.m-confirm-typed { align-self: center; width: calc(100% - 32px); height: auto; max-height: calc(100dvh - 32px); border: 1px solid var(--bd); border-radius: 14px; }
  .modal.is-mobile.m-confirm .mh, .modal.is-mobile.m-confirm-typed .mh { padding-top: 20px; }
  .modal.is-mobile.m-confirm .mb, .modal.is-mobile.m-confirm-typed .mb { flex: 0 1 auto; }
}
</style>
