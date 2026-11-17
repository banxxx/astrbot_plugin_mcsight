import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";

/** Keep an overlaid menu anchored without being clipped by card overflow. */
export function useAnchoredPopup(
  open: Ref<boolean>,
  anchor: Ref<HTMLElement | null>,
  popup: Ref<HTMLElement | null>,
  options: { minWidth?: number; maxHeight?: number } = {},
) {
  const popupStyle = ref<Record<string, string>>({});
  // A dialog owns its overlaid controls as well as its visible form fields.
  // Position:fixed still escapes card overflow without placing the listbox
  // outside the dialog's aria-modal boundary.
  const popupTarget = computed(() => anchor.value?.closest<HTMLElement>('[role="dialog"]') || "body");
  let restoreOnClose = false;
  function reposition() {
    if (!open.value || !anchor.value) return;
    const rect = anchor.value.getBoundingClientRect();
    const padding = 12;
    const width = Math.min(Math.max(rect.width, options.minWidth ?? rect.width), window.innerWidth - padding * 2);
    const left = Math.max(padding, Math.min(rect.left, window.innerWidth - width - padding));
    const below = window.innerHeight - rect.bottom - padding - 6;
    const above = rect.top - padding - 6;
    const wantedHeight = Math.min(popup.value?.scrollHeight || 320, options.maxHeight ?? 320);
    const placeAbove = below < wantedHeight && above > below;
    const maxHeight = Math.max(80, Math.min(options.maxHeight ?? 320, placeAbove ? above : below));
    popupStyle.value = {
      left: `${left}px`,
      width: `${width}px`,
      maxHeight: `${maxHeight}px`,
      ...(placeAbove
        ? { bottom: `${window.innerHeight - rect.top + 6}px`, top: "auto" }
        : { top: `${rect.bottom + 6}px`, bottom: "auto" }),
    };
  }
  function outside(event: PointerEvent) {
    if (!open.value) return;
    const target = event.target as Node;
    if (!anchor.value?.contains(target) && !popup.value?.contains(target)) {
      restoreOnClose = !!popup.value?.contains(document.activeElement);
      open.value = false;
    }
  }
  watch(open, async (value) => {
    if (!value) {
      const needsRestore = restoreOnClose || !!popup.value?.contains(document.activeElement);
      restoreOnClose = false;
      if (needsRestore) {
        await nextTick();
        // Do not steal focus from a different control the user just clicked.
        if (!document.activeElement || document.activeElement === document.body) {
          const trigger = anchor.value?.querySelector<HTMLElement>('button:not(:disabled), [tabindex="0"]');
          const dialog = anchor.value?.closest<HTMLElement>('[role="dialog"]');
          (trigger || dialog)?.focus();
        }
      }
      return;
    }
    reposition();
    await nextTick();
    reposition();
  });
  onMounted(() => {
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
  });
  onBeforeUnmount(() => {
    document.removeEventListener("pointerdown", outside);
    window.removeEventListener("resize", reposition);
    window.removeEventListener("scroll", reposition, true);
  });
  return { popupStyle, popupTarget, reposition };
}
