// Uji nyata: pemetaan part, validasi berkas, backoff, dan KONTRAK ENDPOINT.
//
// Bagian kontrak sengaja diuji dengan fetch tiruan: modul ini sebelumnya
// memanggil endpoint yang tidak pernah ada di backend, dan kegagalannya baru
// terlihat saat runtime. Test di sini mengunci bentuk permintaan supaya
// kesalahan yang sama tidak bisa terulang diam-diam.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  abortRemoteUpload,
  backoffDelay,
  chunkPlan,
  MAX_FILE_SIZE,
  PART_SIZE,
  resumeKey,
  uploadMultipart,
  validateFile,
} from "./multipart";
import { extractYoutubeId, isValidYoutubeUrl } from "./youtube";

describe("chunkPlan", () => {
  it("3 GB pada 10 MB/part => 308 part, part terakhir lebih pendek", () => {
    const threeGb = 3 * 1024 * 1024 * 1024;
    const parts = chunkPlan(threeGb, PART_SIZE);
    expect(parts).toHaveLength(308);
    expect(parts[0]).toEqual({ partNumber: 1, start: 0, end: PART_SIZE });
    const last = parts[307];
    expect(last.partNumber).toBe(308);
    expect(last.end).toBe(threeGb);
    expect(last.end - last.start).toBe(threeGb - 307 * PART_SIZE);
  });

  it("part berurutan 1-based tanpa celah dan tanpa tumpang tindih", () => {
    const parts = chunkPlan(25 * 1024 * 1024, PART_SIZE);
    expect(parts.map((p) => p.partNumber)).toEqual([1, 2, 3]);
    expect(parts[0].end).toBe(parts[1].start);
    expect(parts[1].end).toBe(parts[2].start);
    expect(parts[2].end).toBe(25 * 1024 * 1024);
  });

  it("berkas kecil tetap satu part", () => {
    expect(chunkPlan(1)).toHaveLength(1);
    expect(chunkPlan(1)[0]).toEqual({ partNumber: 1, start: 0, end: 1 });
  });

  it("ukuran tidak sah ditolak", () => {
    expect(() => chunkPlan(0)).toThrow(/positif/);
    expect(() => chunkPlan(100, 0)).toThrow(/positif/);
  });
});

describe("validateFile", () => {
  it("menerima format dan ukuran yang didukung", () => {
    expect(validateFile("klip.mp4", 100)).toBeNull();
    expect(validateFile("KLIP.MOV", 100)).toBeNull();
    expect(validateFile("klip.mkv", 100)).toBeNull();
  });

  it("menolak ekstensi lain", () => {
    expect(validateFile("klip.avi", 100)).toMatch(/Format/);
    expect(validateFile("klip", 100)).toMatch(/Format/);
  });

  it("menolak berkas di atas 3 GB", () => {
    expect(validateFile("klip.mp4", MAX_FILE_SIZE + 1)).toMatch(/3 GB/);
  });

  it("menerima tepat 3 GB dan berkas kosong", () => {
    expect(validateFile("klip.mp4", MAX_FILE_SIZE)).toBeNull();
    expect(validateFile("klip.mp4", 0)).toMatch(/kosong/);
  });
});

describe("backoffDelay", () => {
  it("naik eksponensial lalu berhenti di sekitar 8 detik", () => {
    const first = backoffDelay(1);
    const second = backoffDelay(2);
    const third = backoffDelay(3);
    expect(first).toBeLessThan(second);
    expect(second).toBeLessThan(third);
    // Jitter ditambahkan, jadi batasnya sedikit di atas nilai dasar.
    expect(backoffDelay(10)).toBeLessThanOrEqual(8000 + 250);
  });
});

describe("resumeKey", () => {
  it("stabil untuk berkas yang sama, berubah bila berkas berubah", () => {
    expect(resumeKey("a.mp4", 10, 100)).toBe(resumeKey("a.mp4", 10, 100));
    expect(resumeKey("a.mp4", 10, 100)).not.toBe(resumeKey("a.mp4", 11, 100));
    expect(resumeKey("a.mp4", 10, 100)).not.toBe(resumeKey("b.mp4", 10, 100));
  });
});

// ---------------------------------------------------------------------------
// Kontrak endpoint — inti pengaman terhadap regresi
// ---------------------------------------------------------------------------

type Call = { url: string; method: string; body: unknown };

/** fetch tiruan: server dengan `received` potongan yang sudah ada. */
function mockServer(received: number[] = []): { fetchFn: typeof fetch; calls: Call[] } {
  const calls: Call[] = [];
  const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const path = new URL(url).pathname;
    if (path === "/api/v1/jobs") return new Response(JSON.stringify({ id: "job-1" }), { status: 201 });
    if (path === "/api/v1/uploads/init") {
      return new Response(
        JSON.stringify({ upload_id: "up-1", part_size_bytes: 8, part_count: 2, received_parts: received }),
        { status: 201 },
      );
    }
    if (path.startsWith("/api/v1/uploads/up-1/parts/")) return new Response(null, { status: 204 });
    if (path === "/api/v1/uploads/up-1/complete") {
      return new Response(JSON.stringify({ job_id: "job-1", status: "queued" }), { status: 200 });
    }
    return new Response(JSON.stringify({ detail: `tak terduga: ${url}` }), { status: 500 });
  }) as typeof fetch;
  return { fetchFn, calls };
}

const route = (call: Call) => `${call.method} ${new URL(call.url).pathname}`;

describe("uploadMultipart — kontrak endpoint", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("membuat job, init, PUT tiap potongan, lalu complete — semua di bawah /api/v1", async () => {
    const { fetchFn, calls } = mockServer();
    vi.stubGlobal("fetch", fetchFn);
    vi.stubGlobal("indexedDB", undefined); // Node: tidak ada IndexedDB

    const file = new File([new Uint8Array(12)], "video.mp4", { type: "video/mp4" });
    const ready: string[] = [];
    const result = await uploadMultipart(file, "http://api.test/api/v1", undefined, {
      clipCount: 7,
      onUploadReady: ({ uploadId }) => ready.push(uploadId),
    });

    expect(result).toEqual({ jobId: "job-1", uploadId: "up-1" });
    expect(ready).toEqual(["up-1"]);
    expect(calls.map(route)).toEqual([
      "POST /api/v1/jobs",
      "POST /api/v1/uploads/init",
      "PUT /api/v1/uploads/up-1/parts/1",
      "PUT /api/v1/uploads/up-1/parts/2",
      "POST /api/v1/uploads/up-1/complete",
    ]);
    expect(calls[0].body).toMatchObject({ source_type: "upload", clip_count: 7 });
    expect(calls[1].body).toMatchObject({ job_id: "job-1", filename: "video.mp4", size_bytes: 12 });
  });

  it("resume: potongan yang sudah diterima server tidak dikirim ulang", async () => {
    const { fetchFn, calls } = mockServer([1]);
    vi.stubGlobal("fetch", fetchFn);
    vi.stubGlobal("indexedDB", undefined);

    const progress: number[] = [];
    const file = new File([new Uint8Array(12)], "video.mp4");
    await uploadMultipart(file, "http://api.test/api/v1", undefined, { onProgress: (done) => progress.push(done) });

    expect(calls.map(route)).not.toContain("PUT /api/v1/uploads/up-1/parts/1");
    expect(progress[0]).toBe(8); // progres mulai dari bagian tersimpan, bukan 0
  });

  it("menolak berkas yang tidak lolos validasi sebelum menyentuh jaringan", async () => {
    const { fetchFn, calls } = mockServer();
    vi.stubGlobal("fetch", fetchFn);

    const bad = new File([new Uint8Array(8)], "video.avi", { type: "video/x-msvideo" });
    await expect(uploadMultipart(bad, "http://api.test/api/v1")).rejects.toThrow(/Format/);
    expect(calls).toHaveLength(0);
  });
});

describe("abortRemoteUpload", () => {
  it("memakai DELETE /uploads/{upload_id}", async () => {
    const { fetchFn, calls } = mockServer();
    vi.stubGlobal("indexedDB", undefined);
    await abortRemoteUpload("http://api.test/api/v1", "up-1", undefined, fetchFn);
    expect(calls.map(route)).toEqual(["DELETE /api/v1/uploads/up-1"]);
    vi.unstubAllGlobals();
  });

  it("tidak melempar walau server gagal — pembatalan bersifat upaya terbaik", async () => {
    const fetchFn = (async () => {
      throw new Error("jaringan mati");
    }) as typeof fetch;
    await expect(abortRemoteUpload("http://api.test/api/v1", "u", undefined, fetchFn)).resolves.toBeUndefined();
  });
});

describe("youtube url", () => {
  it("menerima watch dan youtu.be, menolak host/format lain", () => {
    expect(isValidYoutubeUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toBe(true);
    expect(isValidYoutubeUrl("https://youtu.be/dQw4w9WgXcQ")).toBe(true);
    expect(isValidYoutubeUrl("https://example.com/watch?v=dQw4w9WgXcQ")).toBe(false);
    expect(isValidYoutubeUrl("bukan url")).toBe(false);
    expect(extractYoutubeId("https://youtu.be/dQw4w9WgXcQ")).toBe("dQw4w9WgXcQ");
    expect(
      extractYoutubeId("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"),
    ).toBe("dQw4w9WgXcQ");
  });
});
