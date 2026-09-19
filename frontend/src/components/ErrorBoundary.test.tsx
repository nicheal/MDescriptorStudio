// The boundary is the only thing standing between a throwing view and a blank
// webview with no diagnostics in a packaged build, so its own contract is
// pinned here: catch the failure and show a readable message with a way out.
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import ErrorBoundary from "./ErrorBoundary";

function Throw(): ReactNode {
  throw new Error("boom in the page");
}

describe("ErrorBoundary", () => {
  it("replaces the failing subtree with a readable fallback", () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const error = console.error;
    console.error = () => {};
    try {
      act(() => {
        root.render(
          <ErrorBoundary>
            <span>healthy sibling</span>
            <Throw />
          </ErrorBoundary>,
        );
      });
    } finally {
      console.error = error;
    }
    expect(host.textContent).toContain("boom in the page");
    expect(host.textContent).toContain("Reload");
    expect(host.textContent).not.toContain("healthy sibling");
  });
});
