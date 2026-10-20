/* AstrBot 面板注入的桥（plugin_page_bridge.js）：页面在 opaque origin 的 iframe 里，
   拿不到 storage 也不能自己 fetch，所有数据只能走这里。 */

export interface BridgeContext {
  pluginName?: string;
  pageName?: string;
  locale?: string;
  theme?: string;
  username?: string;
  i18n?: Record<string, string>;
  [key: string]: unknown;
}

/* 桥的返回契约（实测内核 plugin_page_bridge.js:174 + PluginViewPage.vue:389）：
   面板已经把后端的 {status,data} 拆掉了 —— resolve 出来就是 data；
   status==="error" 时面板抛错，桥 reject。所以这里不再自己拆信封。 */
export interface RawBridge {
  ready(): Promise<unknown>;
  getContext(): BridgeContext | null;
  getLocale(): string;
  getI18n(): Record<string, string>;
  t(key: string, fallback?: string): string;
  onContext(handler: (ctx: BridgeContext) => void): () => void;
  apiGet<T = unknown>(endpoint: string, params?: Record<string, string | number>): Promise<T>;
  apiPost<T = unknown>(endpoint: string, body?: unknown): Promise<T>;
  upload<T = unknown>(endpoint: string, file: File): Promise<T>;
  download<T = unknown>(endpoint: string, params?: Record<string, string | number>, filename?: string): Promise<T>;
  subscribeSSE(endpoint: string, handlers: Record<string, unknown>, params?: Record<string, string | number>): Promise<string>;
  unsubscribeSSE(id: string): Promise<unknown>;
}

export class ApiError extends Error {}

export function getBridge(): RawBridge | null {
  const w = window as unknown as { AstrBotPluginView?: RawBridge; AstrBotPluginPage?: RawBridge };
  return w.AstrBotPluginView || w.AstrBotPluginPage || null;
}

/** 把桥的 reject（面板用 Error 承载后端 message）统一成 ApiError，调用方只管拿数据。 */
export async function call<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new ApiError(message || "桥调用失败");
  }
}
