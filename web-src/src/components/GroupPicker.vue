<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from "vue";
import UiIcon from "./UiIcon.vue";
import { S } from "../store";
import { useAnchoredPopup } from "./useAnchoredPopup";

const open = ref(false);
const filter = ref("");
const active = ref(-1);
const box = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const search = ref<HTMLInputElement | null>(null);
const popup = ref<HTMLElement | null>(null);
const listId = `groups-${useId()}`;
const list = computed(() => {
  const query = filter.value.trim();
  if (!query) return S.groups;
  return S.groups.filter((group) => group.gid.includes(query) || group.alias?.includes(query));
});
const { popupStyle, popupTarget, reposition } = useAnchoredPopup(open, box, popup, { minWidth: 280, maxHeight: 360 });
function countOf(group: { gid: string; server_count: number }) {
  return group.gid === S.gid && S.group?.gid === group.gid ? S.group.servers.length : group.server_count;
}
function pick(gid: string) {
  S.gid = gid;
  open.value = false;
  trigger.value?.focus();
}
function toggle() {
  if (!S.groups.length) return;
  open.value = !open.value;
  if (open.value) {
    filter.value = "";
    active.value = list.value.findIndex((group) => group.gid === S.gid);
    void nextTick(() => { reposition(); search.value?.focus(); });
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault(); event.stopPropagation(); open.value = false; trigger.value?.focus();
  } else if (event.key === "Tab") {
    open.value = false;
    trigger.value?.focus();
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!open.value) { toggle(); return; }
    if (!list.value.length) return;
    const step = event.key === "ArrowDown" ? 1 : -1;
    active.value = (active.value + step + list.value.length) % list.value.length;
    void nextTick(() => popup.value?.querySelector<HTMLElement>("[data-active='true']")?.scrollIntoView({ block: "nearest" }));
  } else if (event.key === "Enter" && open.value) {
    event.preventDefault();
    const group = list.value[active.value >= 0 ? active.value : 0];
    if (group) pick(group.gid);
  }
}
watch(filter, () => { active.value = list.value.length ? 0 : -1; });
</script>

<template>
  <div ref="box" class="pick">
    <button ref="trigger" class="trig" :class="{ on: open }" type="button" aria-haspopup="listbox"
      :aria-expanded="open" :aria-controls="listId" aria-label="选择当前群" :disabled="!S.groups.length" @click="toggle" @keydown="keydown">
      <span>{{ S.gid || "尚无群配置" }}</span>
      <span class="chevron"><UiIcon :name="open ? 'chevron-up' : 'chevron-down'" :size="16" /></span>
    </button>
    <Teleport :to="popupTarget">
      <div v-if="open" ref="popup" class="group-menu" :style="popupStyle" @keydown="keydown">
        <div class="group-filter">
          <UiIcon name="search" :size="16" />
          <input ref="search" v-model="filter" aria-label="输入群号筛选" placeholder="输入群号筛选" inputmode="numeric" />
        </div>
        <div :id="listId" class="group-list" role="listbox" aria-label="群列表">
          <button v-for="(group, index) in list" :key="group.gid" class="group-option" :class="{ selected: group.gid === S.gid, active: index === active }"
            type="button" role="option" :aria-selected="group.gid === S.gid" :data-active="index === active" tabindex="-1"
            @pointerdown.prevent @pointermove="active = index" @click="pick(group.gid)">
            <span class="selection"><UiIcon v-if="group.gid === S.gid" name="check" :size="16" /></span>
            <span class="group-main">
              <span class="group-id">{{ group.gid }}</span>
              <span v-if="group.alias" class="group-alias">{{ group.alias }}</span>
            </span>
            <span class="count">{{ countOf(group) }} 台服务器</span>
          </button>
          <div v-if="!list.length" class="group-empty">没有匹配的群号</div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pick { position: relative; width: 280px; max-width: 100%; min-width: 0; }
.trig { display: flex; align-items: center; gap: 8px; width: 100%; height: var(--h-ctl); padding: 0 10px 0 12px; border: 1px solid var(--ctl-bd); border-radius: 8px; background: var(--surface); color: var(--ink); font-size: 13px; text-align: left; cursor: pointer; transition: border-color var(--transition), box-shadow var(--transition); }
.trig > span:first-child { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trig .chevron { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 28px; height: 24px; border-left: 1px solid #e9ecf3; color: var(--ink3); }
.trig.on, .trig:focus-visible { outline: none; border-color: var(--focus); box-shadow: 0 0 0 1px var(--focus), 0 0 4px var(--focus-ring); }
.trig:disabled { color: var(--ink4); background: var(--disabled-bg); border-color: var(--disabled-bd); cursor: not-allowed; }
.group-menu { position: fixed; z-index: 100; display: flex; flex-direction: column; gap: 6px; padding: 6px; border: 1px solid var(--bd); border-radius: 10px; background: var(--surface); box-shadow: 0 6px 18px #24335312; }
.group-filter { display: flex; align-items: center; gap: 8px; flex: none; height: 40px; padding: 0 10px; border: 1px solid var(--ctl-bd); border-radius: 8px; color: var(--ink3); }
.group-filter:focus-within { border-color: var(--focus); }
.group-filter input { width: 0; min-width: 0; flex: 1 1 auto; padding: 0; border: none; outline: none; background: none; color: var(--ink); font-size: 13px; }
.group-filter input::placeholder { color: var(--ink3); }
.group-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
.group-option { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 40px; padding: 6px 10px; border: none; border-radius: 6px; background: none; color: var(--ink); font-size: 13px; text-align: left; cursor: pointer; }
.group-option .selection { display: inline-flex; flex: none; width: 16px; color: var(--ac); }
.group-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 2px; height: 40px; }
.group-id { font-size: 13px; line-height: 20px; }
.group-alias { color: var(--ink3); font-size: 12px; line-height: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-option .count { color: var(--ink3); font-size: 12px; }
.group-option.selected { background: var(--ac-soft); color: var(--ac); font-weight: 500; }
.group-option.active:not(.selected), .group-option:hover:not(.selected) { background: #f8f9ff; }
.group-empty { padding: 12px; font-size: 12px; color: var(--ink3); }
@media (max-width: 900px), (max-height: 520px) {
  .pick { width: 100%; }
  .trig, .group-filter, .group-option { min-height: 44px; }
}
</style>
