// Halaman detail job (Review Studio) — keadaan yang dilihat pengguna:
// in-flight job, error + coba lagi, dan keadaan kosong yang menjelaskan.
//
// Konteks regresi: job yang MASIH BERJALAN pernah ditampilkan seolah
// "analisis selesai tetapi tidak menemukan apa pun". Server menandai
// alasannya lewat `note` pada daftar segmen, dan catatan itu harus tampil
// apa adanya — termasuk nama tahap yang tersimpan di basis data
// ("ingest", "transcribe", "analyze", ...).
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import JobPage from "@/app/jobs/[id]/page";
import { emitStatus, installEventSourceStub, installFetchStub, jsonResponse, type Routes } from "@/lib/test/halaman";
import type { Job, JobStage, Render, Segment } from "@/lib/types";

// `useRouter` Next.js hanya hidup di dalam App Router; test merender halaman
// secara langsung, jadi peruteannya diganti tiruan. Halaman memakainya hanya
// saat menghapus job (tidak diuji di sini).
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
}));

const JOB_ID = "aaaabbbb-0000-1111-2222-333344445555";
const JOB_PATH = `GET /api/v1/jobs/${JOB_ID}`;
const SEGMENTS_PATH = `GET /api/v1/jobs/${JOB_ID}/segments`;
const RENDERS_PATH = `GET /api/v1/jobs/${JOB_ID}/renders`;

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: JOB_ID,
    user_id: "u-1",
    source_type: "youtube",
    source_url: "https://youtu.be/abc",
    status: "queued",
    stage: "upload",
    progress: 0,
    clip_count: 5,
    error: null,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
    ...overrides,
  };
}

function makeSegment(overrides: Partial<Segment> = {}): Segment {
  return {
    id: "seg-1111",
    start_s: 12.5,
    end_s: 52.5,
    score: 0.9,
    label: "Hook pembuka yang kuat",
    hook_score: 0.8,
    completeness: 0.7,
    emotional_arc: 0.6,
    reason: "Pertanyaan retoris di awal memancing rasa ingin tahu.",
    status: "proposed",
    ...overrides,
  };
}

/** `note` dihitung seperti backend: alasan daftar segmen kosong. */
function segmentNoteFor(job: Job): string {
  if (job.status === "failed") return job.error ?? "Job gagal sebelum analisis menghasilkan segmen.";
  if (job.status === "queued" || job.status === "running") {
    return `Job masih pada tahap '${job.stage}'. Segmen muncul setelah transkripsi dan analisis selesai.`;
  }
  return "Analisis selesai tetapi tidak ada segmen tersimpan. Klik 'Analisis ulang' untuk mencoba lagi.";
}

interface Scenario {
  job: Job;
  segments?: Segment[];
  renders?: Render[];
}

/** Route lengkap untuk satu job; note mengikuti isi daftar segmen. */
function jobRoutes(scenario: Scenario): Routes {
  const segments = scenario.segments ?? [];
  return {
    [JOB_PATH]: () => jsonResponse(scenario.job),
    [SEGMENTS_PATH]: () =>
      jsonResponse({ items: segments, total: segments.length, note: segments.length === 0 ? segmentNoteFor(scenario.job) : "" }),
    [RENDERS_PATH]: () => jsonResponse({ items: scenario.renders ?? [], total: (scenario.renders ?? []).length }),
  };
}

function renderJobPage() {
  installEventSourceStub();
  return render(<JobPage params={Promise.resolve({ id: JOB_ID })} />);
}

describe("Halaman detail job (Review Studio)", () => {
  it("job berjalan: badge progres + catatan alasan belum ada klip, BUKAN 'analisis tidak menemukan apa pun'", async () => {
    const running = makeJob({ status: "running", stage: "analyze", progress: 42 });
    installFetchStub(jobRoutes({ job: running }));

    renderJobPage();

    expect(await screen.findByText("Diproses · 42%")).toBeDefined();
    expect(screen.getByText("Belum ada klip")).toBeDefined();
    expect(
      screen.getByText("Job masih pada tahap 'analyze'. Segmen muncul setelah transkripsi dan analisis selesai."),
    ).toBeDefined();
    expect(screen.queryByText(/Analisis selesai tetapi tidak ada segmen/)).toBeNull();
    // Selama masih diproses, tombolnya adalah Batalkan — bukan Proses ulang.
    expect(screen.getByRole("button", { name: "Batalkan" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Proses ulang" })).toBeNull();
  });

  it("tiap tahap basis data memakai kalimat 'masih pada tahap' yang benar", async () => {
    for (const stage of ["ingest", "transcribe", "analyze", "render"] as JobStage[]) {
      const running = makeJob({ status: "running", stage, progress: 10 });
      installFetchStub(jobRoutes({ job: running }));

      const view = renderJobPage();
      expect(await screen.findByText(`Job masih pada tahap '${stage}'. Segmen muncul setelah transkripsi dan analisis selesai.`)).toBeDefined();
      view.unmount();
    }
    // Tidak ada nama tahap berakhiran -ing yang bocor ke layar.
    expect(screen.queryByText(/tahap 'transcribing'/)).toBeNull();
    expect(screen.queryByText(/tahap 'analyzing'/)).toBeNull();
  });

  it("job selesai dengan segmen: menampilkan klip, skor, dan rasio siap unduh", async () => {
    const done = makeJob({ status: "done", stage: "done", progress: 100 });
    const segment = makeSegment();
    const render: Render = {
      id: "render-1",
      segment_id: segment.id,
      kind: "preview",
      r2_key: null,
      preset: null,
      status: "running",
      duration_ms: null,
      size_bytes: null,
      created_at: "2026-09-29T01:00:00Z",
      crop_mode: "face_track",
    };
    installFetchStub(jobRoutes({ job: done, segments: [segment], renders: [render] }));

    renderJobPage();

    expect(await screen.findByRole("heading", { name: "Hook pembuka yang kuat" })).toBeDefined();
    expect(screen.getByText("0:12 – 0:52")).toBeDefined();
    expect(screen.getByText("40 detik")).toBeDefined();
    expect(screen.getByText("Pertanyaan retoris di awal memancing rasa ingin tahu.")).toBeDefined();
    // Render (preview) masih berjalan: kartu pemutar menunjukkan status, bukan pesan galat.
    expect(screen.getByText("Merender klip")).toBeDefined();
    expect(screen.queryByText("Belum ada klip")).toBeNull();
  });

  it("job selesai tanpa segmen: menyebut 'Analisis selesai' dan menawarkan Pilih ulang", async () => {
    const done = makeJob({ status: "done", stage: "done", progress: 100 });
    installFetchStub(jobRoutes({ job: done }));

    renderJobPage();

    expect(await screen.findByText("Belum ada klip")).toBeDefined();
    expect(
      screen.getByText("Analisis selesai tetapi tidak ada segmen tersimpan. Klik 'Analisis ulang' untuk mencoba lagi."),
    ).toBeDefined();
    expect(screen.getByRole("button", { name: "Analisis ulang" })).toBeDefined();
  });

  it("render final dibatalkan pengguna tidak tampil sebagai 'Render gagal'; render yang benar-benar gagal tetap tampil", async () => {
    const canceledJob = makeJob({ status: "canceled", stage: "render", progress: 80, error: "Dibatalkan pengguna." });
    const segment = makeSegment();
    const finalRender = (status: Render["status"]): Render => ({
      id: `render-${status}`,
      segment_id: segment.id,
      kind: "final",
      r2_key: null,
      preset: null,
      status,
      duration_ms: null,
      size_bytes: null,
      created_at: "2026-09-29T01:00:00Z",
      crop_mode: "face_track",
    });

    installFetchStub(jobRoutes({ job: canceledJob, segments: [segment], renders: [finalRender("canceled")] }));
    const view = renderJobPage();
    expect(await screen.findByRole("heading", { name: "Hook pembuka yang kuat" })).toBeDefined();
    expect(screen.queryByText("Render gagal")).toBeNull();
    // Klip bisa dirender lagi setelah dibatalkan.
    expect(screen.getByRole("button", { name: "Render ulang" })).toBeDefined();
    view.unmount();

    installFetchStub(jobRoutes({ job: makeJob({ status: "done", stage: "done", progress: 100 }), segments: [segment], renders: [finalRender("failed")] }));
    renderJobPage();
    expect(await screen.findByText("Render gagal")).toBeDefined();
  });

  it("job gagal: pesan error job tampil utuh dan Proses ulang tersedia", async () => {
    const failed = makeJob({
      status: "failed",
      stage: "render",
      progress: 60,
      error: "Terhenti karena aplikasi ditutup saat job berjalan. Klik 'Proses ulang'.",
    });
    installFetchStub(jobRoutes({ job: failed }));

    renderJobPage();

    const alerts = await screen.findAllByText(/Terhenti karena aplikasi ditutup/);
    expect(alerts.length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Proses ulang" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Batalkan" })).toBeNull();
  });

  it("error memuat job: pesan yang bisa dibaca, lalu pulih setelah event SSE memicu muat ulang", async () => {
    const job = makeJob({ status: "done", stage: "done", progress: 100 });
    const routes: Routes = {
      [JOB_PATH]: () => jsonResponse({ detail: "Job tidak ditemukan." }, 404),
      [SEGMENTS_PATH]: () => jsonResponse({ items: [], total: 0, note: segmentNoteFor(job) }),
      [RENDERS_PATH]: () => jsonResponse({ items: [], total: 0 }),
    };
    const stub = installFetchStub(routes);

    renderJobPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Job tidak ditemukan.");
    expect(stub.calls).toContain(JOB_PATH);

    // Halaman tidak punya tombol retry manual; pemulihannya lewat event SSE
    // yang memicu load ulang — inilah jalur yang diuji.
    routes[JOB_PATH] = () => jsonResponse(job);
    emitStatus();

    expect(await screen.findByRole("button", { name: "Analisis ulang" })).toBeDefined();
    await waitFor(() => expect(screen.queryByText("Job tidak ditemukan.")).toBeNull());
  });

  it("memuat: tabel/panel kerangka tampil sebelum data tiba", async () => {
    const done = makeJob({ status: "running", stage: "ingest", progress: 20 });
    installFetchStub(jobRoutes({ job: done }));

    renderJobPage();

    // Data segmen belum ada: skeleton, bukan tabel.
    expect(document.querySelector(".skeleton")).not.toBeNull();
    expect(await screen.findByText("Diproses · 20%")).toBeDefined();
  });
});
