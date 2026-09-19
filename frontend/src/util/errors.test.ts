import { describe, expect, it } from "vitest";
import { describeError } from "./errors";

describe("describeError", () => {
  it("shows the reference that joins the message to the backend log", () => {
    // frames.response_err deliberately keeps the structured diagnosis off the
    // wire and logs it against error_id instead, so error_id is the only thing
    // that connects what a user saw to the line that explains it.
    expect(describeError({ code: "DATASET_CHANGED", message: "Dataset changed on disk.", error_id: "7f3a" }, "DATASET"))
      .toBe("DATASET_CHANGED: Dataset changed on disk. (7f3a)");
  });

  it("falls back to the caller's words when the frame has none", () => {
    const err = { code: "INVALID_PARAMS" };
    expect(describeError(err, "DATASET_VIEW", "Could not save dataset view"))
      .toBe("INVALID_PARAMS: Could not save dataset view");
    // A rejection the client synthesises (BACKEND_DOWN before any request left)
    // has neither an id nor a message from the sidecar.
    expect(describeError({}, "EXPORT", "export failed")).toBe("EXPORT: export failed");
    expect(describeError(null, "ANALYSIS")).toBe("ANALYSIS: ANALYSIS");
  });

  it("keeps an empty frame message from rendering as a bare code twice", () => {
    expect(describeError({ code: "", message: "" }, "RESULT", "delete failed")).toBe("RESULT: delete failed");
  });
});
