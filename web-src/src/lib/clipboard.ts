/* 复制文本：同步选区复制优先，Clipboard API 兜底。
   插件页跑在 AstrBot 的 opaque origin iframe 里（见 bridge.ts），
   navigator.clipboard 多半不存在或被权限策略拒绝；document.execCommand("copy")
   在用户点击手势内仍然可用，因此不能只依赖 Clipboard API。 */

function isIos(): boolean {
  return /iP(?:hone|ad|od)/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

export function copyWithSelection(text: string): boolean {
  if (!text) return false;
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.setAttribute("aria-hidden", "true");
  Object.assign(area.style, {
    position: "fixed", top: "0", left: "0", width: "1px", height: "1px",
    padding: "0", border: "0", opacity: "0", fontSize: "16px",
  });
  document.body.appendChild(area);
  try {
    area.focus();
    if (isIos()) {
      /* iOS Safari 对只读 textarea 的 select() 选区不生效，改用 Range（clipboard.js 同款处理）。 */
      area.readOnly = false;
      area.contentEditable = "true";
      const range = document.createRange();
      range.selectNodeContents(area);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    } else {
      area.select();
    }
    area.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
    previousFocus?.focus();
  }
}

/** 必须在用户点击手势内同步调用同步路径；await 之后仍可继续尝试 Clipboard API。 */
export async function copyText(text: string): Promise<boolean> {
  if (copyWithSelection(text)) return true;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 权限策略不允许时抛 NotAllowedError，继续返回失败。 */
  }
  return false;
}
