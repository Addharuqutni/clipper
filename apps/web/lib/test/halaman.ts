// Test halaman — mock di BATAS JARINGAN.
//
// Semua halaman mengambil data lewat `fetch` (apps/web/lib/api/client.ts),
// jadi `vi.stubGlobal("fetch", ...)` sudah cukup: modul klien asli tetap
// dipakai, termasuk pemetaan URL, parsing JSON, dan ApiError. Yang diuji
// adalah output yang dilihat pengguna, bukan pemanggilan fungsi internal.
import { afterEach, vi } from "vitest";

type Route = (request: { url: string; method: string; body: unknown }) => Response | undefined;

/** Route keyed by "METHOD /path" (tanpa query). */
export type Routes = Record<string, Route>;

export interface FetchStub {
  fetchFn: typeof fetch;
  /** Urutan "METHOD /path" dalam urutan pemanggilan — bukti re-fetch. */
  calls: string[];
}

/**
 * Pasang `fetch` tiruan. Route yang tidak cocok menghasilkan 500 berisi
 * detail yang menunjuk URL-nya, supaya kesalahan konfigurasi test terlihat.
 */
export function installFetchStub(routes: Routes): FetchStub {
  const calls: string[] = [];
  const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" || input instanceof URL ? String(input) : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    let path = url;
    try {
      path = new URL(url).pathname;
    } catch {
      // Bukan URL absolut; pakai apa adanya untuk pesan kesalahan.
    }
    const key = `${method} ${path}`;
    calls.push(key);

    const route = routes[key];
    const response = route?.({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    if (response) return response;

    return new Response(JSON.stringify({ detail: `route test tidak dikenal: ${key}` }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  vi.stubGlobal("fetch", fetchFn);
  return { fetchFn, calls };
}

/** Response JSON dengan status tertentu. */
export function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Response kesalahan FastAPI: `{"detail": "..."}`. */
export function errorResponse(status: number, detail: string): Response {
  return jsonResponse({ detail }, status);
}

const statusListeners = new Set<(message: { data: string }) => void>();

/**
 * EventSource tiruan untuk SSE.
 *
 * jsdom tidak menyediakan EventSource, dan halaman job memakainya untuk
 * memuat ulang saat server mengirim kabar. `emitStatus()` memicu ulang
 * pemuatan persis seperti event "status" sungguhan; `onerror` sengaja TIDAK
 * dipanggil otomatis karena itu memicu sambung-ulang ber-timeout yang
 * mengotori test.
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(public readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (message: { data: string }) => void): void {
    if (type === "status") statusListeners.add(listener);
  }

  removeEventListener(type: string, listener: (message: { data: string }) => void): void {
    if (type === "status") statusListeners.delete(listener);
  }

  close(): void {}
}

/** Pasang EventSource tiruan; panggil sebelum render. */
export function installEventSourceStub(): void {
  statusListeners.clear();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
}

/** Kirim event "status" seperti dari server; halaman akan memuat ulang. */
export function emitStatus(payload: unknown = { stage: "analyze" }): void {
  for (const listener of statusListeners) listener({ data: JSON.stringify(payload) });
}

/** Bersihkan stub setelah tiap test. */
afterEach(() => {
  vi.unstubAllGlobals();
  statusListeners.clear();
});
