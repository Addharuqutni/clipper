// Dashboard — keadaan yang dilihat pengguna: memuat, error + coba lagi,
// kosong, dan daftar job dengan nama tahap apa adanya dari basis data.
//
// Konteks regresi: daftar tahap yang SALAH pernah membuat job yang sedang
// berjalan tampak seperti "analisis selesai tetapi tidak menemukan apa pun".
// Test di sini mengunci nama tahap yang tersimpan di DB ("ingest",
// "transcribe", "analyze", "render") beserta labelnya di UI.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import Dashboard from "@/app/dashboard/page";
import { installFetchStub, jsonResponse, errorResponse, type Routes } from "@/lib/test/halaman";
import type { Job } from "@/lib/types";

const JOBS_PATH = "GET /api/v1/jobs";

/** Job dengan nilai bawaan yang wajar; field yang diuji ditulis eksplisit. */
function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "12345678-90ab-cdef-1234-567890abcdef",
    user_id: "u-1",
    source_type: "youtube",
    source_url: "https://youtu.be/abc",
    video_title: null,
    status: "queued",
    stage: "upload",
    progress: 0,
    clip_count: 5,
    language: "id",
    // Selalu dikirim backend (JobResponse.live_minutes); `null` = video biasa.
    // Dijaga oleh apps/api/tests/test_frontend_types.py.
    live_minutes: null,
    error: null,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
    ...overrides,
  };
}

function listResponse(items: Job[]): unknown {
  return { items, total: items.length, limit: 50, offset: 0 };
}

describe("Dashboard", () => {
  it("menampilkan keadaan kosong yang menjelaskan langkah berikutnya", async () => {
    installFetchStub({ [JOBS_PATH]: () => jsonResponse(listResponse([])) });

    render(<Dashboard />);

    expect(await screen.findByRole("heading", { name: "Belum ada job" })).toBeDefined();
    expect(screen.getByText(/Mulai dengan menempel tautan YouTube/)).toBeDefined();
    expect(screen.getByRole("link", { name: "Dari YouTube" }).getAttribute("href")).toBe("/youtube");
    expect(screen.getByRole("link", { name: "Unggah berkas" }).getAttribute("href")).toBe("/upload");
  });

  it("menampilkan dampak loading dulu, lalu tabel job terbaru", async () => {
    installFetchStub({
      [JOBS_PATH]: () =>
        jsonResponse(listResponse([makeJob({ id: "aaaaaaaa-0000-0000-0000-000000000000", status: "done", stage: "done" })])),
    });

    render(<Dashboard />);

    // Sebelum data tiba: kerangka, bukan layar kosong.
    expect(screen.getByText("Memuat job…")).toBeDefined();

    expect(await screen.findByRole("cell", { name: "aaaaaaaa…" })).toBeDefined();
    expect(screen.getByRole("heading", { name: "Job terbaru" })).toBeDefined();
    expect(screen.getByText("1 job ditampilkan")).toBeDefined();
  });

  it("menampilkan judul YouTube dan nama file upload pada setiap job", async () => {
    const youtube = makeJob({ id: "y0000000-0000-0000-0000-000000000000", video_title: "Judul Video YouTube" });
    const upload = makeJob({
      id: "u0000000-0000-0000-0000-000000000000",
      source_type: "upload",
      source_url: null,
      video_title: "rekaman-podcast.mp4",
    });
    installFetchStub({ [JOBS_PATH]: () => jsonResponse(listResponse([youtube, upload])) });

    render(<Dashboard />);

    expect(await screen.findByText("Judul Video YouTube")).toBeDefined();
    expect(screen.getByText("rekaman-podcast.mp4")).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "Video" })).toBeDefined();
  });

  it("job tanpa judul menampilkan 'Menunggu metadata', bukan sel kosong", async () => {
    installFetchStub({
      [JOBS_PATH]: () =>
        jsonResponse(listResponse([makeJob({ id: "n0000000-0000-0000-0000-000000000000", video_title: null })])),
    });

    render(<Dashboard />);

    const row = (await screen.findByRole("cell", { name: "n0000000…" })).closest("tr");
    if (!row) throw new Error("baris job tidak ditemukan");
    expect(within(row).getByText("Menunggu metadata")).toBeDefined();
  });

  it("judul panjang tetap utuh di DOM dan disimpan sebagai tooltip", async () => {
    const longTitle = "Kupas Tuntas Strategi Konten 2026: dari Ide Mentah sampai Klip Viral Setiap Hari";
    installFetchStub({
      [JOBS_PATH]: () =>
        jsonResponse(listResponse([makeJob({ id: "t0000000-0000-0000-0000-000000000000", video_title: longTitle })])),
    });

    render(<Dashboard />);

    // Pemotongan elipsis murni visual (CSS); teks dan tooltip tetap penuh.
    const cell = await screen.findByTitle(longTitle);
    expect(cell.textContent).toBe(longTitle);
    expect(cell.getAttribute("title")).toBe(longTitle);
    expect(cell.className).toContain("cell-truncate");
  });

  it("job berjalan menampilkan nama tahap, progres, dan status — bukan 'selesai tanpa hasil'", async () => {
    const running = makeJob({ id: "bbbbbbbb-1111-1111-1111-111111111111", status: "running", stage: "analyze", progress: 55 });
    installFetchStub({ [JOBS_PATH]: () => jsonResponse(listResponse([running])) });

    render(<Dashboard />);

    const row = (await screen.findByRole("cell", { name: "bbbbbbbb…" })).closest("tr");
    if (!row) throw new Error("baris job tidak ditemukan");
    const cells = within(row);

    expect(cells.getByText("Memilih momen")).toBeDefined();
    expect(cells.getByText("55%")).toBeDefined();
    expect(cells.getByText("Diproses")).toBeDefined();
    // Regresi yang pernah terjadi: job berjalan ditampilkan seolah selesai.
    expect(cells.queryByText("Selesai")).toBeNull();
  });

  it("menerjemahkan semua tahap yang tersimpan di basis data", async () => {
    const stages: { stage: Job["stage"]; label: string }[] = [
      { stage: "upload", label: "Menunggu unggahan" },
      { stage: "ingest", label: "Mengambil video" },
      { stage: "transcribe", label: "Transkripsi" },
      { stage: "analyze", label: "Memilih momen" },
      { stage: "render", label: "Merender klip" },
      { stage: "done", label: "Selesai" },
    ];
    const items = stages.map(({ stage }, index) =>
      makeJob({
        id: `${String(index).repeat(8)}-0000-0000-0000-000000000000`,
        status: stage === "render" ? "running" : "queued",
        stage,
      }),
    );
    installFetchStub({ [JOBS_PATH]: () => jsonResponse(listResponse(items)) });

    render(<Dashboard />);

    await screen.findByRole("cell", { name: "00000000…" });
    for (const { label } of stages) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    // Tidak ada nama internal mentah yang bocor ke layar.
    expect(screen.queryByText("analyze")).toBeNull();
    expect(screen.queryByText("transcribe")).toBeNull();
  });

  it("menghitung statistik: total, sedang diproses, selesai, dan gagal", async () => {
    const items = [
      makeJob({ id: "c0000000-0000-0000-0000-000000000000", status: "running", stage: "transcribe", progress: 30 }),
      makeJob({ id: "d0000000-0000-0000-0000-000000000000", status: "done", stage: "done", progress: 100 }),
      makeJob({ id: "e0000000-0000-0000-0000-000000000000", status: "failed", stage: "render", error: "FFmpeg keluar dengan kode 1" }),
      // `queued` pada tahap upload belum dihitung sebagai "sedang diproses".
      makeJob({ id: "f0000000-0000-0000-0000-000000000000", status: "queued", stage: "upload" }),
    ];
    installFetchStub({ [JOBS_PATH]: () => jsonResponse(listResponse(items)) });

    render(<Dashboard />);

    await screen.findByRole("cell", { name: "c0000000…" });
    // "Selesai" juga muncul sebagai label tahap di tabel, jadi statistik
    // dibatasi ke grid ringkasan.
    const statsGrid = document.querySelector(".grid-4");
    if (!statsGrid) throw new Error("grid statistik tidak ditemukan");
    const stats = (label: string) => within(statsGrid as HTMLElement).getByText(label).parentElement?.textContent;
    expect(stats("Total job")).toContain("4");
    expect(stats("Sedang diproses")).toContain("1");
    expect(stats("Selesai")).toContain("1");
    expect(stats("Gagal")).toContain("1");
    // Pesan kesalahan job ikut terlihat pada barisnya.
    expect(screen.getByText("FFmpeg keluar dengan kode 1")).toBeDefined();
  });

  it("menampilkan pesan error yang bisa dibaca, lalu berhasil setelah 'Coba lagi'", async () => {
    const failing: Routes = {
      [JOBS_PATH]: () => errorResponse(503, "Backend sedang sibuk."),
    };
    const stub = installFetchStub(failing);

    render(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Backend sedang sibuk.");
    expect(stub.calls).toEqual([JOBS_PATH]);

    // Backend pulih; menekan "Coba lagi" harus MEMUAT ULANG dari jaringan.
    const recovered = makeJob({ id: "99999999-0000-0000-0000-000000000000", status: "done", stage: "done" });
    failing[JOBS_PATH] = () => jsonResponse(listResponse([recovered]));

    await userEvent.click(screen.getByRole("button", { name: "Coba lagi" }));

    expect(await screen.findByRole("cell", { name: "99999999…" })).toBeDefined();
    await waitFor(() => expect(stub.calls).toEqual([JOBS_PATH, JOBS_PATH]));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("menampilkan pesan jaringan yang ramah saat fetch gagal total", async () => {
    installFetchStub({
      [JOBS_PATH]: () => {
        throw new TypeError("Failed to fetch");
      },
    });

    render(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Tidak dapat menghubungi server API. Pastikan backend berjalan.",
    );
    expect(screen.getByRole("button", { name: "Coba lagi" })).toBeDefined();
  });
});
