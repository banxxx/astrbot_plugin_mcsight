<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowDown, ArrowUp, ArrowUpDown, BookOpen, Braces, Check, ChevronDown, ChevronUp,
  ChevronRight, CircleAlert, CircleCheck, Code, CodeXml, Compass, Copy, Download, Eye, EyeOff,
  Info, KeyRound, List, ListChecks, ListTree, LoaderCircle, Lock, Menu, MoreHorizontal,
  Palette, Pencil, Pickaxe, Plus, RefreshCw, RotateCw, Search, Server, ShieldCheck,
  SlidersHorizontal, Trash2, TriangleAlert, Upload, User, Users, X,
} from "@lucide/vue";
import { S } from "../store";
import { CREAM_ICON_MAP, PACK_ICONS } from "../lib/pack-icons";
const MAP: Record<string, unknown> = {
  "arrow-down": ArrowDown, "arrow-up": ArrowUp, "arrow-up-down": ArrowUpDown,
  "book-open": BookOpen, check: Check, "chevron-down": ChevronDown,
  "chevron-up": ChevronUp, "chevron-right": ChevronRight, braces: Braces,
  code: Code, "code-xml": CodeXml, copy: Copy, download: Download, eye: Eye, "eye-off": EyeOff,
  upload: Upload,
  info: Info, key: KeyRound, "key-round": KeyRound, list: List,
  "list-checks": ListChecks, "list-tree": ListTree, lock: Lock,
  "lock-keyhole": Lock, "more-horizontal": MoreHorizontal, ellipsis: MoreHorizontal,
  plus: Plus, refresh: RefreshCw, "refresh-cw": RefreshCw, "rotate-cw": RotateCw,
  search: Search, server: Server, sliders: SlidersHorizontal,
  "sliders-horizontal": SlidersHorizontal, trash: Trash2, "trash-2": Trash2,
  alert: TriangleAlert, "triangle-alert": TriangleAlert, "circle-alert": CircleAlert,
  "circle-check": CircleCheck, "check-circle": CircleCheck, "alert-circle": CircleAlert, "loader-circle": LoaderCircle,
  user: User, users: Users, menu: Menu, pencil: Pencil, "shield-check": ShieldCheck, x: X,
  palette: Palette, compass: Compass, pickaxe: Pickaxe,
};
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 16 });
const comp = computed(() => MAP[props.name] || null);
/* 奶油主题用 Game Icon Pack 的填充图标；没有对应图标时退回线性图标，
   保证新增逻辑图标不会在另一套皮肤里凭空消失。 */
const packPaths = computed(() => {
  if (S.skin !== "cream") return [];
  const packName = CREAM_ICON_MAP[props.name];
  return packName ? PACK_ICONS[packName] ?? [] : [];
});
/* 填充图标在 10×10 画布里留白比线性图标多，放大一点才和同尺寸文字齐平。 */
const packSize = computed(() => Math.round(props.size * 1.12));
</script>

<template>
  <svg v-if="packPaths.length" class="ui-icon-pack" :width="packSize" :height="packSize" viewBox="0 0 10 10"
    fill="currentColor" aria-hidden="true">
    <path v-for="(d, index) in packPaths" :key="index" :d="d" fill-rule="nonzero" />
  </svg>
  <component :is="comp" v-else-if="comp" :size="size" :stroke-width="2" aria-hidden="true" />
</template>

<style scoped>
.ui-icon-pack { display: block; flex: none; overflow: visible; }
</style>
