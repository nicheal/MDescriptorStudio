import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import "./global.css";
import { theme } from "./theme";
import App from "./App";
import { setAppIcon } from "./brand";
import { useI18n } from "./i18n";

setAppIcon();

/** Applies the antd locale pack for the selected UI language around the app. */
function LocalizedApp() {
  const lang = useI18n((s) => s.lang);
  return (
    <ConfigProvider theme={theme} locale={lang === "zh" ? zhCN : enUS}>
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LocalizedApp />
  </React.StrictMode>,
);
