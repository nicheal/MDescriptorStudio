// Typed IPC client over Tauri (docs/plan/04 §5).
// The Rust bridge serializes the protocol frame; the webview never writes raw
// bytes to the backend pipe.
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { PROTOCOL_VERSION, type ErrorFrame } from "../types/protocol";

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: ErrorFrame) => void;
}

interface ProtocolFrame {
  protocol_version?: number;
  id?: number;
  result?: unknown;
  error?: ErrorFrame;
  event?: string;
  data?: unknown;
}

export const BACKEND_MESSAGE_EVENT = "backend-message";
export const BACKEND_EXIT_EVENT = "backend-exit";
const MAX_ORPHAN_FRAMES = 128;

class IpcClient {
  private pending = new Map<number, Pending>();
  private orphanFrames = new Map<number, ProtocolFrame>();
  private eventHandlers = new Map<string, Set<(data: unknown) => void>>();
  private connected = false;

  get isReady() {
    return this.connected;
  }

  async connect(onExit?: () => void): Promise<void> {
    await listen<string>(BACKEND_MESSAGE_EVENT, (evt) => this.onLine(evt.payload));
    await listen<void>(BACKEND_EXIT_EVENT, () => {
      this.connected = false;
      for (const [, p] of this.pending) p.reject({ code: "BACKEND_DOWN", message: "backend exited" });
      this.pending.clear();
      this.orphanFrames.clear();
      onExit?.();
    });
    this.connected = true;
  }

  private onLine(line: string) {
    let frame: ProtocolFrame;
    try {
      frame = JSON.parse(line);
    } catch {
      console.error("unparseable backend frame");
      return;
    }
    if (!frame || typeof frame !== "object" || frame.protocol_version !== PROTOCOL_VERSION) return;
    if (typeof frame.event === "string") {
        this.eventHandlers.get(frame.event)?.forEach((h) => h(frame.data));
        return;
    }
    if (typeof frame.id !== "number" || !Number.isSafeInteger(frame.id)) return;
    const p = this.pending.get(frame.id);
    if (!p) {
      if (this.orphanFrames.size >= MAX_ORPHAN_FRAMES) {
        const oldest = this.orphanFrames.keys().next().value;
        if (typeof oldest === "number") this.orphanFrames.delete(oldest);
      }
      this.orphanFrames.set(frame.id, frame);
      return;
    }
    this.pending.delete(frame.id);
    if (frame.error) p.reject(frame.error);
    else p.resolve(frame.result);
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.connected) {
      return Promise.reject({ code: "BACKEND_DOWN", message: "backend not running" });
    }
    return new Promise<T>((resolve, reject) => {
      invoke<number>("backend_request", { method, params })
        .then((id) => {
          if (!Number.isSafeInteger(id) || id < 0) {
            reject({ code: "BACKEND_DOWN", message: "backend returned an invalid request id" });
            return;
          }
          const frame = this.orphanFrames.get(id);
          if (frame) {
            this.orphanFrames.delete(id);
            if (frame.error) reject(frame.error);
            else resolve(frame.result as T);
            return;
          }
          this.pending.set(id, {
            resolve: resolve as (v: unknown) => void,
            reject,
          });
        })
        .catch(() => {
          reject({ code: "BACKEND_DOWN", message: "backend request failed" });
        });
    });
  }

  on(event: string, handler: (data: unknown) => void): () => void {
    let set = this.eventHandlers.get(event);
    if (!set) {
      set = new Set();
      this.eventHandlers.set(event, set);
    }
    set.add(handler);
    return () => set!.delete(handler);
  }

  /** Feed a raw protocol line captured before the listener attached. */
  processLine(line: string) {
    this.onLine(line);
  }
}

export const ipc = new IpcClient();
