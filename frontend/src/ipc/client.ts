// NDJSON IPC client over Tauri (docs/plan/04 §5).
// Requests: invoke("backend_send", { line }) -> Rust writes to backend stdin.
// Output: Rust forwards each stdout line as "backend-message" event.
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

class IpcClient {
  private nextId = 1;
  private pending = new Map<number, Pending>();
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
      onExit?.();
    });
    this.connected = true;
  }

  private onLine(line: string) {
    let frame: ProtocolFrame;
    try {
      frame = JSON.parse(line);
    } catch {
      console.error("unparseable backend frame:", line);
      return;
    }
    if (frame.event) {
      this.eventHandlers.get(frame.event)?.forEach((h) => h(frame.data));
      return;
    }
    if (typeof frame.id === "number" && this.pending.has(frame.id)) {
      const p = this.pending.get(frame.id)!;
      this.pending.delete(frame.id);
      if (frame.error) p.reject(frame.error);
      else p.resolve(frame.result);
    }
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.connected) {
      return Promise.reject({ code: "BACKEND_DOWN", message: "backend not running" });
    }
    const id = this.nextId++;
    const frame = { protocol_version: PROTOCOL_VERSION, id, method, params };
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: resolve as (v: unknown) => void,
        reject,
      });
      invoke("backend_send", { line: JSON.stringify(frame) }).catch((e) => {
        this.pending.delete(id);
        reject({ code: "BACKEND_DOWN", message: String(e) });
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
}

export const ipc = new IpcClient();
