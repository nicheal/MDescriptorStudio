// Keep the in-app brand mark and the browser/WebView favicon on the same
// source asset as the native Tauri icons.
export const APP_ICON_URL = new URL("../../src-tauri/icons/icon.png", import.meta.url).href;

export function setAppIcon(): void {
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.type = "image/png";
  link.href = APP_ICON_URL;
}
