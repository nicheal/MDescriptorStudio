// Typed IPC client over Tauri (docs/plan/04 §5).
// The Rust bridge serializes the protocol frame; the webview never writes raw
// bytes to the backend pipe.
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { PROTOCOL_VERSION, type ErrorFrame } from "../types/protocol";

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: ErrorFrame) => void;
}

interface PendingInvoke {
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
  // backend_request resolves with an id asynchronously. Keep the caller's
  // reject function until then so backend exit can settle requests whose id
  // has not arrived yet.
  private pendingInvokes = new Set<PendingInvoke>();
  private orphanFrames = new Map<number, ProtocolFrame>();
  private eventHandlers = new Map<string, Set<(data: unknown) => void>>();
  private connected = false;
  private connecting: Promise<void> | null = null;
  private connectionGeneration = 0;
  private unlistenMessage: UnlistenFn | null = null;
  private unlistenExit: UnlistenFn | null = null;
  private onExit: ((logDir: string | null) => void) | undefined;

  get isReady() {
    return this.connected;
  }

  async connect(onExit?: (logDir: string | null) => void): Promise<void> {
    this.onExit = onExit;
    if (this.connected) return;
    if (this.connecting) return this.connecting;

    const generation = ++this.connectionGeneration;
    const connection = (async () => {
      const unlistenMessage = await listen<string>(BACKEND_MESSAGE_EVENT, (evt) => {
        if (generation === this.connectionGeneration) this.onLine(evt.payload, generation);
      });
      if (generation !== this.connectionGeneration) {
        unlistenMessage();
        return;
      }
      this.unlistenMessage = unlistenMessage;
      try {
        // The shell names its log directory on exit: in a release build it has
        // no console, so this event is the only way a failure can be reported at
        // all, and a path the user can open is the difference between a bug
        // report and a guess.
        const unlistenExit = await listen<{ logDir?: string | null }>(BACKEND_EXIT_EVENT, (evt) =>
          this.handleBackendExit(generation, typeof evt?.payload?.logDir === "string" ? evt.payload.logDir : null),
        );
        if (generation !== this.connectionGeneration) {
          unlistenMessage();
          unlistenExit();
          if (this.unlistenMessage === unlistenMessage) this.unlistenMessage = null;
          if (this.unlistenExit === unlistenExit) this.unlistenExit = null;
          return;
        }
        this.unlistenExit = unlistenExit;
      } catch (error) {
        unlistenMessage();
        if (this.unlistenMessage === unlistenMessage) this.unlistenMessage = null;
        throw error;
      }
      this.connected = true;
    })();
    this.connecting = connection;
    void connection.then(
      () => {
        if (this.connecting === connection) this.connecting = null;
      },
      () => {
        if (this.connecting === connection) this.connecting = null;
      },
    );
    return connection;
  }

  private handleBackendExit(generation: number, logDir: string | null) {
    if (generation !== this.connectionGeneration) return;
    this.connectionGeneration += 1;
    this.connected = false;
    this.connecting = null;
    this.rejectPending({ code: "BACKEND_DOWN", message: "backend exited" });

    const unlistenMessage = this.unlistenMessage;
    const unlistenExit = this.unlistenExit;
    this.unlistenMessage = null;
    this.unlistenExit = null;
    unlistenMessage?.();
    unlistenExit?.();
    const onExit = this.onExit;
    this.onExit = undefined;
    onExit?.(logDir);
  }

  /** Release native listeners when Vite replaces this module during HMR. */
  disconnect() {
    this.connectionGeneration += 1;
    this.connected = false;
    this.connecting = null;
    this.rejectPending({ code: "BACKEND_DOWN", message: "backend disconnected" });
    const unlistenMessage = this.unlistenMessage;
    const unlistenExit = this.unlistenExit;
    this.unlistenMessage = null;
    this.unlistenExit = null;
    unlistenMessage?.();
    unlistenExit?.();
    this.eventHandlers.clear();
    this.onExit = undefined;
  }

  private rejectPending(error: ErrorFrame) {
    for (const [, p] of this.pending) p.reject(error);
    this.pending.clear();
    for (const invocation of this.pendingInvokes) invocation.reject(error);
    this.pendingInvokes.clear();
    this.orphanFrames.clear();
  }

  private onLine(line: string, generation = this.connectionGeneration) {
    if (generation !== this.connectionGeneration) return;
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
    const requestGeneration = this.connectionGeneration;
    return new Promise<T>((resolve, reject) => {
      const invocation = { reject };
      this.pendingInvokes.add(invocation);
      invoke<number>("backend_request", { method, params })
        .then((id) => {
          this.pendingInvokes.delete(invocation);
          if (requestGeneration !== this.connectionGeneration || !this.connected) {
            reject({ code: "BACKEND_DOWN", message: "backend disconnected" });
            return;
          }
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
          this.pendingInvokes.delete(invocation);
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

const hot = (import.meta as ImportMeta & { hot?: { dispose: (callback: () => void) => void } }).hot;
if (hot) {
  hot.dispose(() => ipc.disconnect());
}
