<script setup lang="ts">
/* 弹窗规矩：遮罩点击与 Esc 都不关闭，只走按钮；同档位内尺寸恒定，
   高度按该档位最重状态定高；窄屏允许宽度收缩（max-width），高度不变。
   标题/副标题的字体与颜色按画布逐格分：等价命令行＝等宽+主色，说明行＝正体+灰。 */
import { onBeforeUnmount, onMounted } from "vue";
import UiIcon from "./UiIcon.vue";
import { useIsMobile } from "../lib/useMediaQuery";

withDefaults(
  defineProps<{
    cls?: "m-confirm" | "m-confirm-typed" | "m-form" | "m-list" | "m-json" | "m-help";
    title: string;
    titleMono?: boolean;
    sub?: string;
    subMono?: boolean;
    subNote?: boolean;
    info?: string;
  }>(),
  { cls: "m-form", titleMono: false, subMono: false, subNote: false },
);

const emit = defineEmits<{ (e: "close"): void }>();
const isMobile = useIsMobile();

/* 弹窗打开期间锁住背后的文档滚动：手机上弹窗是整屏 sheet、内部 .mb 自己滚，
   不锁的话头部/底部和滚动链会把背景页面一起带着滚，出现两条滚动条。
   用 fixed body + 记录 scrollY 的方式，兼容 iOS Safari 对 overflow:hidden 的忽略。 */
let lockDepth = 0;
let lockedScrollY = 0;
let lockedPadding = "";

function lockPageScroll() {
  if (lockDepth++ > 0) return;
  const body = document.body;
  lockedScrollY = window.scrollY || document.documentElement.scrollTop || 0;
  lockedPadding = body.style.paddingRight;
  const scrollbar = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
  body.style.position = "fixed";
  body.style.top = `-${lockedScrollY}px`;
  body.style.left = "0";
  body.style.right = "0";
  body.style.width = "100%";
  if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;
  document.documentElement.classList.add("modal-open");
}

function unlockPageScroll() {
  if (lockDepth === 0) return;
  lockDepth -= 1;
  if (lockDepth > 0) return;
  const body = document.body;
  body.style.position = "";
  body.style.top = "";
  body.style.left = "";
  body.style.right = "";
  body.style.width = "";
  body.style.paddingRight = lockedPadding;
  document.documentElement.classList.remove("modal-open");
  window.scrollTo(0, lockedScrollY);
}

onMounted(lockPageScroll);
onBeforeUnmount(unlockPageScroll);
</script>

<template>
  <div class="veil" :class="{ 'is-mobile': isMobile }">
    <div class="modal" :class="[cls, { 'is-mobile': isMobile }]" role="dialog" aria-modal="true" :aria-label="title">
      <div class="mh">
        <h3 :class="titleMono ? 'mono' : ''">{{ title }}</h3>
        <div v-if="sub" class="ms" :class="[subMono ? 'mono' : '', subNote ? 'note' : '']">{{ sub }}</div>
        <button v-if="isMobile" class="mx" type="button" aria-label="关闭" @click="emit('close')">
          <UiIcon name="x" :size="20" />
        </button>
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
}

.m-confirm {
  width: 440px;
  height: 260px;
}

/* 删除群配置：确认提示 + 输入框，比普通确认弹窗高一档（实测内容高 199 + 页头页脚） */
.m-confirm-typed {
  width: 440px;
  height: 340px;
}

.m-form {
  width: 520px;
  /* 该档位最重状态 = 编辑服务器：6 个字段 + 「备注用 MOTD」的两行（开关行 + 值行）+ 未配端口的红条。
     实测内容自然高 590，红条在场时最挤，按 590+页头页脚定高。 */
  height: 725px;
}

.m-list {
  width: 520px;
  height: 500px;
}

.m-json {
  width: 760px;
  height: 500px;
}

/* 说明类弹窗：内容静态、只有一段滚动，按内容最重状态定高 */
.m-help {
  width: 640px;
  height: 680px;
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

.ms.note {
  color: var(--ink3);
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

.m-form .mb {
  gap: 14px;
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

@media (max-width: 560px) {
  .modal {
    width: calc(100% - 24px);
  }
}
</style>
