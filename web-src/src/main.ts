import { createApp } from "vue";
import App from "./App.vue";
import { applySkin } from "./store";
import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/parts.css";
import "./styles/table.css";
import "./styles/mobile.css";
import "./styles/cream.css";

/* 先落默认皮肤，偏好读回来后再切换；避免首帧缺 data-skin。 */
applySkin("classic");
createApp(App).mount("#app");
