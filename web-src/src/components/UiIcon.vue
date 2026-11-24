<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowDown, ArrowUp, ArrowUpDown, BookOpen, Braces, Check, ChevronDown, ChevronUp,
  ChevronRight, CircleAlert, CircleCheck, Code, CodeXml, Copy, Download, Eye, EyeOff,
  Info, KeyRound, List, ListChecks, ListTree, LoaderCircle, Lock, Menu, MoreHorizontal,
  Pencil, Plus, RefreshCw, RotateCw, Search, Server, ShieldCheck, SlidersHorizontal,
  Trash2, TriangleAlert, Upload, User, Users, X,
} from "@lucide/vue";
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
};
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 16 });
const comp = computed(() => MAP[props.name] || null);
</script>

<template>
  <component :is="comp" v-if="comp" :size="size" :stroke-width="2" aria-hidden="true" />
</template>
