import { onBeforeUnmount, ref } from "vue";

/* 手机布局判定：窄屏，或矮屏（手机横屏）。页面跑在 AstrBot 的 iframe 里，
   matchMedia 量的就是 iframe 自己的视口，和 CSS 里的断点口径一致。 */
export const MOBILE_QUERY = "(max-width: 900px), (max-height: 520px)";

export function useMediaQuery(query: string) {
  let mq: MediaQueryList | null = null;
  try {
    mq = typeof window.matchMedia === "function" ? window.matchMedia(query) : null;
  } catch {
    mq = null;
  }
  const matches = ref(mq ? mq.matches : false);
  if (!mq) return matches;

  const onChange = (e: MediaQueryListEvent) => {
    matches.value = e.matches;
  };

  /* MediaQueryList.addEventListener 是 Chrome 84 / Safari 14 才有的；
     部分安卓 WebView 和旧版手机浏览器只有 addListener，缺了会让整个页面白屏。
     注册本身也可能在个别内核上抛错，所以两种方式都兜住，最坏情况只是不跟随旋转。 */
  const useModern = typeof mq.addEventListener === "function";
  const useLegacy = typeof mq.addListener === "function";
  if (useModern) {
    try {
      mq.addEventListener("change", onChange);
      onBeforeUnmount(() => mq.removeEventListener("change", onChange));
      return matches;
    } catch {
      /* 继续尝试旧接口 */
    }
  }
  if (useLegacy) {
    try {
      mq.addListener(onChange);
      onBeforeUnmount(() => mq.removeListener(onChange));
    } catch {
      /* 监听不上就只保留初始 matches，不影响页面渲染 */
    }
  }
  return matches;
}

export function useIsMobile() {
  return useMediaQuery(MOBILE_QUERY);
}
