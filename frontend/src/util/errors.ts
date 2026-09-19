import type { ErrorFrame } from "../types/protocol";

/**
 * One line for the user: what failed, and the reference that explains it.
 *
 * The renderer used to build this string in seventeen places, and none of them
 * carried `error_id` - the one field that joins a complaint to the backend log
 * line holding the structured diagnosis (`frames.response_err` deliberately keeps
 * that off the wire). The fallback code and message stay with the caller, because
 * each call site knows which operation failed and what to say if the frame was
 * too thin to say anything.
 */
export function describeError(error: Partial<ErrorFrame> | null | undefined, fallbackCode: string, fallbackMessage?: string): string {
  const code = error?.code || fallbackCode;
  const message = error?.message || fallbackMessage || code;
  return error?.error_id ? `${code}: ${message} (${error.error_id})` : `${code}: ${message}`;
}
