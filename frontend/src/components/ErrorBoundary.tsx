// Render-error isolation. Without a boundary a single throwing view replaces
// the whole webview with a blank page, and a packaged release has no devtools
// and no stderr (the sidecar's is discarded), so the user is left with nothing
// to read and nothing to report.
import { Component, type ReactNode } from "react";
import { Button, Typography } from "antd";
import { useT } from "../i18n";

function Fallback({ error }: { error: Error }) {
  const { t } = useT();
  return (
    <div style={{ padding: 24, maxWidth: 680 }}>
      <Typography.Paragraph strong>{t("This view could not be displayed")}</Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        {t("The dataset and its computations are unaffected. Reload to continue where you left off.")}
      </Typography.Paragraph>
      <Typography.Paragraph code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {error.message}
      </Typography.Paragraph>
      <Button type="primary" onClick={() => window.location.reload()}>
        {t("Reload")}
      </Button>
    </div>
  );
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error("view failed to render", error, info);
  }

  render() {
    if (this.state.error) return <Fallback error={this.state.error} />;
    return this.props.children;
  }
}
