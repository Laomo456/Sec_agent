import { createPinia } from "pinia";
import { createApp } from "vue";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/minimap/dist/style.css";
import "@vue-flow/node-resizer/dist/style.css";
import App from "./App.vue";
import "./style.css";

createApp(App).use(createPinia()).mount("#app");
