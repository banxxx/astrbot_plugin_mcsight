<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import UiIcon from "./UiIcon.vue";
import { S } from "../store";

const open = ref(false);
const filter = ref("");
const box = ref<HTMLElement | null>(null);

const list = computed(() => {
  const q = filter.value.trim();
  return q ? S.groups.filter((g) => g.gid.includes(q)) : S.groups;
});

const currentCount = computed(() => {
  const hit = S.groups.find((g) => g.gid === S.gid);
  return hit ? hit.server_count : 0;
});

function pick(gid: string) {
  S.gid = gid;
  open.value = false;
}

// 面板沙箱里不能靠 blur，用外部点击收起
function onDocClick(e: MouseEvent) {
  const node = box.value;
  if (open.value && node && !node.contains(e.target as Node)) {
    open.value = false;
  }
}

document.addEventListener("click", onDocClick);
onBeforeUnmount(() => document.removeEventListener("click", onDocClick));
</script>

<template>
  <div ref="box" class="pick">
    <button class="trig" :class="{ on: open }" type="button" @click="open = !open">
      <b>{{ S.gid || "无群" }}</b>
      <i>· {{ currentCount }} 台</i>
      <UiIcon :name="open ? 'chevron-up' : 'chevron-down'" :size="14" />
    </button>

    <div v-if="open" class="panel">
      <div class="filter">
        <UiIcon name="search" :size="13" />
        <input v-model="filter" placeholder="输入群号筛选" />
      </div>
      <div class="list">
        <button
          v-for="g in list"
          :key="g.gid"
          class="it"
          :class="{ on: g.gid === S.gid }"
          type="button"
          @click="pick(g.gid)"
        >
          <span>{{ g.gid }}</span>
          <em>{{ g.server_count }} 台</em>
        </button>
        <div v-if="!list.length" class="empty">没有匹配的群号</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pick {
  position: relative;
}

.trig {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--ac-line);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--ink2);
  font-family: var(--ff);
  font-size: 11px;
  cursor: pointer;
}

.trig.on {
  border-color: var(--ac);
}

/* 图标本身不吃指针：chevron 中间是透明的，落在空隙上时命中判定会漂到别处 */
.trig svg {
  pointer-events: none;
}

.trig b {
  color: var(--ac);
  font-family: var(--ff);
  font-size: 13px;
  font-weight: 600;
}

.trig i {
  font-style: normal;
  color: var(--ink3);
}

.panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 320px;
  max-height: 336px;
  padding: 8px;
  border: 1px solid var(--bd);
  border-radius: 8px;
  background: var(--surface);
}

.filter {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink4);
}

.filter input {
  flex: 1 1 auto;
  min-width: 0;
  border: none;
  outline: none;
  background: none;
  color: var(--ink);
  font-family: var(--ff);
  font-size: 12px;
}

.list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.it {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-ctl);
  background: none;
  color: var(--ink);
  font-family: var(--ff);
  font-size: 13px;
  cursor: pointer;
}

.it em {
  color: var(--ink3);
  font-family: var(--ff);
  font-size: 11px;
  font-style: normal;
}

.it:hover {
  background: var(--thead);
}

.it.on {
  background: var(--ac-soft);
  color: var(--ac);
  font-weight: 600;
}

.it.on em {
  color: var(--ac);
}
</style>
