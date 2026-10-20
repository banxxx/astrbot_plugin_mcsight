import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// AstrBot 把页面挂在 /views/config/_t/<token>/ 这种带尾斜杠的路径下，
// 相对引用能自己解析，所以 base 必须是 './'；产物文件名不带 hash，
// 因为内核资源头写死 Cache-Control: no-store，浏览器根本不缓存，hash 只会污染仓库 diff。
export default defineConfig({
  base: "./",
  plugins: [vue()],
  build: {
    outDir: "../views/config",
    emptyOutDir: true,
    target: "es2020",
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/app[extname]",
      },
    },
  },
});
