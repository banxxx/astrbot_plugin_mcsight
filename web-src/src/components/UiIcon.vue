<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Code,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  List,
  Lock,
  Plus,
  RefreshCw,
  Search,
  Server,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from "@lucide/vue";

/* 画布里的图标全部来自 lucide，这里用同一套字形保证观感一致。 */
const MAP: Record<string, unknown> = {
  "arrow-down": ArrowDown,
  "arrow-up": ArrowUp,
  "arrow-up-down": ArrowUpDown,
  "book-open": BookOpen,
  check: Check,
  "chevron-down": ChevronDown,
  "chevron-up": ChevronUp,
  code: Code,
  eye: Eye,
  "eye-off": EyeOff,
  info: Info,
  key: KeyRound,
  list: List,
  lock: Lock,
  plus: Plus,
  refresh: RefreshCw,
  search: Search,
  server: Server,
  sliders: SlidersHorizontal,
  alert: TriangleAlert,
  x: X,
};

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 15 });
const comp = computed(() => MAP[props.name] || null);
</script>

<template>
  <component :is="comp" v-if="comp" :size="size" :stroke-width="2" />
</template>
