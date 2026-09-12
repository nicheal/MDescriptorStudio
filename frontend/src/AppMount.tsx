// Single mounting path for the app: antd theme + locale providers around App,
// wrapped in StrictMode. Shared by the production entry (main.tsx) and the
// dev-only browser preview (preview.tsx), which imports it asynchronously.
import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import { theme } from "./theme";
import { useI18n } from "./i18n";

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

export function mountApp(root: HTMLElement) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <LocalizedApp />
    </React.StrictMode>,
  );
}
