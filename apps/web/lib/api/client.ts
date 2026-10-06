// Klien API typed + SSE. TECH_SPEC §1: REST + SSE progress.
//
// PENTING: semua router backend didaftarkan di bawah prefix `/api/v1`
// (lihat apps/api/app/api/router.py). Path di klien ini karena itu harus
// menyertakan prefix tersebut — sebelumnya tidak, sehingga tidak ada satu pun
// permintaan yang akan sampai ke endpoint yang benar.
import type {
  AiProviderInfo,
  AiProviderTestResult,
  CookieValidationResult,
  JobLanguage,
  CropModeInfo,
  Job,
  JobEvent,
  JobListResponse,
  MediaInfo,
  SegmentListResponse,
  Render,
  LetterboxPreview,
  SaveAiSettingsPayload,
  SavedAiSettings,
  TranscriptResponse,
  WordUpdateResponse,
  Segment,
  SocialCaption,
  CaptionPreset,
  CaptionPresetList,
  CaptionStyle,
  CaptionStyleOptions,
  FontAssetList,
  JobCaptionStyle,
  JobLogList,
  OverlayAssetList,
  OverlayOptions,
  OverlayPlacement,
  OverlayPlacementList,
  OverlaySuggestResult,
} from "../types";


export const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const API_BASE = `${API_ORIGIN}/api/v1`;

/** Kesalahan API dengan status dan pesan yang bisa ditampilkan ke pengguna. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `Permintaan gagal (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Ambil pesan kesalahan dari respons.
 *
 * Backend FastAPI mengembalikan `{"detail": "..."}` untuk kesalahan yang
 * disengaja, tetapi `detail` bisa berbentuk daftar objek validasi Pydantic.
 * Menampilkan bentuk mentahnya ke pengguna akan membingungkan, jadi bentuk
 * itu diratakan menjadi kalimat.
 */
function extractDetail(payload: unknown): string {
  if (typeof payload !== "object" || payload === null) return "";
  const detail = (payload as { detail?: unknown }).detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "object" && item !== null) {
          const message = (item as { msg?: unknown }).msg;
          const location = (item as { loc?: unknown }).loc;
          const where = Array.isArray(location) ? location.join(".") : "";
          return typeof message === "string" ? `${where}: ${message}`.trim() : "";
        }
        return String(item);
      })
      .filter(Boolean)
      .join("; ");
  }

  return "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    // content-type JSON hanya bila ada badan JSON: pada GET/DELETE header ini
    // memaksa preflight CORS di setiap panggilan, dan pada FormData ia merusak
    // boundary yang seharusnya ditetapkan browser.
    const jsonBody = typeof init.body === "string";
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(jsonBody ? { "content-type": "application/json" } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, "Tidak dapat menghubungi server API. Pastikan backend berjalan.");
  }

  if (!response.ok) {
    let detail = "";
    try {
      detail = extractDetail(await response.json());
    } catch {
      // Badan respons bukan JSON; gunakan pesan bawaan.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Unggah berkas sebagai multipart/form-data (cookies, font, aset overlay). */
export function uploadFile<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  for (const [name, value] of Object.entries(fields)) form.append(name, value);
  return request<T>(path, { method: "POST", body: form });
}

export const api = {
  base: API_BASE,

  // --- Job ---
  listJobs: () => request<JobListResponse>("/jobs"),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  /** Riwayat tahapan job untuk panel log di halaman detail. */
  getJobLog: (id: string) => request<JobLogList>(`/jobs/${id}/events`),
  /** Segmen yang diusulkan untuk sebuah job, terurut skor menurun. */
  listSegments: (id: string) => request<SegmentListResponse>(`/jobs/${id}/segments`),
  /** Transkrip per kata untuk editor teks. */
  getTranscript: (id: string) => request<TranscriptResponse>(`/jobs/${id}/transcript`),
  /**
   * Perbaiki ejaan satu kata; kirim teks kosong untuk menghapusnya.
   * `expectedText` = teks lama menurut klien; server menolak (409) bila
   * transkripnya sudah berubah di tempat lain, alih-alih mengubah kata yang salah.
   */
  updateWord: (id: string, index: number, text: string, expectedText: string) =>
    request<WordUpdateResponse>(`/jobs/${id}/transcript/words/${index}`, {
      method: "PATCH",
      body: JSON.stringify({ text, expected_text: expectedText }),
    }),
  /** Quick edit segmen: potong awal/akhir, atau pilih/tolak. */
  updateSegment: (
    jobId: string,
    segmentId: string,
    patch: { start_s?: number; end_s?: number; status?: "proposed" | "selected" | "rejected" },
  ) =>
    request<Segment>(`/jobs/${jobId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  /** Caption + hashtag AI untuk unggahan klip (FR-4.2). */
  socialCaption: (jobId: string, segmentId: string) =>
    request<SocialCaption>(`/jobs/${jobId}/segments/${segmentId}/social-caption`, { method: "POST" }),
  /**
   * URL pemutaran video sumber untuk elemen `<video>`.
   *
   * Mengembalikan string (bukan memanggil API) karena `<video src>` memerlukan
   * URL, bukan promise. Endpoint ini mendukung HTTP Range sehingga seeking
   * berfungsi.
   */
  jobMediaFileUrl: (id: string) => `${API_BASE}/jobs/${id}/media/file`,

  // --- Gaya teks / caption ---
  /** Pilihan animasi, nilai bawaan, dan rentang yang sah. */
  captionOptions: () => request<CaptionStyleOptions>("/captions/presets/options"),
  listCaptionPresets: () => request<CaptionPresetList>("/captions/presets"),
  createCaptionPreset: (name: string, style: Partial<CaptionStyle>) =>
    request<CaptionPreset>("/captions/presets", {
      method: "POST",
      body: JSON.stringify({ name, style }),
    }),
  updateCaptionPreset: (id: string, patch: { name?: string; style?: Partial<CaptionStyle> }) =>
    request<CaptionPreset>(`/captions/presets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteCaptionPreset: (id: string) =>
    request<void>(`/captions/presets/${id}`, { method: "DELETE" }),
  setDefaultCaptionPreset: (presetId: string | null) =>
    request<CaptionPresetList>("/captions/presets/default", {
      method: "POST",
      body: JSON.stringify({ preset_id: presetId }),
    }),
  /** Gaya efektif sebuah job (dari job, preset bawaan, atau bawaan sistem). */
  getJobCaptionStyle: (jobId: string) =>
    request<JobCaptionStyle>(`/captions/jobs/${jobId}/style`),
  /** Tetapkan override gaya untuk satu job; `null` menghapus override. */
  setJobCaptionStyle: (jobId: string, style: Partial<CaptionStyle> | null) =>
    request<JobCaptionStyle>(`/captions/jobs/${jobId}/style`, {
      method: "PUT",
      body: JSON.stringify({ style }),
    }),

  // --- Font kustom ---
  listFonts: () => request<FontAssetList>("/fonts"),
  deleteFont: (id: string) => request<void>(`/fonts/${id}`, { method: "DELETE" }),

  // --- Overlay B-roll / efek suara ---
  overlayOptions: () => request<OverlayOptions>("/overlays/options"),
  listOverlayAssets: () => request<OverlayAssetList>("/overlays/assets"),
  deleteOverlayAsset: (id: string) =>
    request<void>(`/overlays/assets/${id}`, { method: "DELETE" }),
  /** Penempatan overlay pada sebuah segmen. */
  listOverlays: (segmentId: string) =>
    request<OverlayPlacementList>(`/overlays/segments/${segmentId}`),
  createOverlay: (segmentId: string, payload: {
    asset_id: string; start_s: number; end_s: number;
    position?: string; scale?: number; opacity?: number;
    transition?: string; transition_ms?: number;
  }) =>
    request<OverlayPlacement>(`/overlays/segments/${segmentId}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateOverlay: (placementId: string, patch: Partial<{
    start_s: number; end_s: number; position: string; scale: number;
    opacity: number; transition: string; transition_ms: number;
  }>) =>
    request<OverlayPlacement>(`/overlays/placements/${placementId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteOverlay: (placementId: string) =>
    request<void>(`/overlays/placements/${placementId}`, { method: "DELETE" }),
  /** Usulkan overlay otomatis dari kata kunci transkrip segmen. */
  suggestOverlays: (segmentId: string) =>
    request<OverlaySuggestResult>(`/overlays/segments/${segmentId}/suggest`, { method: "POST" }),
  /**
   * Metadata media sumber. Mengembalikan `null` bila tahap ingest belum
   * menghasilkan metadata — keadaan yang sah, bukan kesalahan.
   */
  getJobMedia: (id: string) => request<MediaInfo | null>(`/jobs/${id}/media`),
  /** Proses ulang job yang gagal/dibatalkan dari tahap ingest. */
  redispatchJob: (id: string) =>
    request<Job>(`/jobs/${id}/dispatch`, { method: "POST" }),
  /** Jalankan ulang analisis AI memakai transkrip yang sudah ada. */
  rescoreJob: (id: string) => request<Job>(`/jobs/${id}/rescore`, { method: "POST" }),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  /** Hapus job beserta media dan hasil render di folder output/<judul>-<id>. */
  deleteJob: (id: string) => request<void>(`/jobs/${id}`, { method: "DELETE" }),
  submitYoutube: (url: string, clipCount = 5, language: JobLanguage = "id", liveMinutes: number | null = null) =>
    request<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify({
        source_type: "youtube",
        source_url: url,
        clip_count: clipCount,
        language,
        live_minutes: liveMinutes,
      }),
    }),

  // --- Render (Pratinjau & Final) ---
  listJobRenders: (jobId: string) =>
    request<{ items: Render[]; total: number }>(`/jobs/${jobId}/renders`),
  renderSegment: (
    jobId: string,
    segmentId: string,
    payload: { kind?: "preview" | "final"; crop_mode?: string; preset?: string } = {},
  ) =>
    request<Render>(`/jobs/${jobId}/segments/${segmentId}/render`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  jobRenderFileUrl: (jobId: string, renderId: string) =>
    `${API_BASE}/jobs/${jobId}/renders/${renderId}/file`,
  jobRenderDownloadUrl: (jobId: string, renderId: string) =>
    `${API_BASE}/jobs/${jobId}/renders/${renderId}/download`,

  // --- Reframe (mode crop) ---
  listCropModes: () =>
    request<{ modes: CropModeInfo[]; default: string }>("/reframe/modes"),
  previewCrop: (sourceWidth: number, sourceHeight: number, mode: string) =>
    request<LetterboxPreview>("/reframe/preview", {
      method: "POST",
      body: JSON.stringify({
        source_width: sourceWidth,
        source_height: sourceHeight,
        mode,
      }),
    }),

  // --- AI provider ---
  listProviders: () =>
    request<{ presets: AiProviderInfo[] }>("/ai/providers"),
  /** Pengaturan tersimpan, atau `null` bila pengguna belum pernah menyimpan. */
  getAiSettings: () => request<SavedAiSettings | null>("/ai/settings"),
  saveAiSettings: (payload: SaveAiSettingsPayload) =>
    request<SavedAiSettings>("/ai/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteAiSettings: () =>
    request<void>("/ai/settings", { method: "DELETE" }),
  testProvider: (payload: {
    preset: string;
    base_url?: string;
    model?: string;
    api_key?: string;
    allow_private_host?: boolean;
  }) => request<AiProviderTestResult>("/ai/test", {
    method: "POST",
    body: JSON.stringify(payload),
  }),

  // --- YouTube ---
  validateYoutubeUrl: (url: string) =>
    request<{ valid: boolean; video_id: string; message: string; is_short: boolean }>(
      "/youtube/validate-url",
      { method: "POST", body: JSON.stringify({ url }) },
    ),
  uploadCookies: (file: File) =>
    uploadFile<CookieValidationResult>("/youtube/cookies", file),
  cookieStatus: () => request<{ stored: boolean }>("/youtube/cookies"),
  deleteCookies: () => request<void>("/youtube/cookies", { method: "DELETE" }),
  cookieRequirements: () =>
    request<{ required_any_of: string[]; explanation: string; max_file_bytes: number }>(
      "/youtube/cookie-requirements",
    ),
};

/**
 * SSE progres job dengan backoff eksponensial dan batas percobaan.
 *
 * Server mengirim event BERNAMA (`event: status` / `event: end`). Browser hanya
 * meneruskan event bernama lewat `addEventListener(nama)` — `onmessage` hanya
 * menerima event tanpa nama, jadi memakainya membuat callback tidak pernah
 * terpanggil. Setelah `end`, stream ditutup dan TIDAK disambung ulang.
 */
export function subscribeJobStream(
  jobId: string,
  onEvent: (ev: JobEvent) => void,
  onError?: (e: Event) => void,
): () => void {
  const MAX_DELAY_MS = 30_000;
  const MAX_ATTEMPTS = 10;

  let source: EventSource | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const handle = (message: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(message.data) as JobEvent);
    } catch {
      // Frame non-JSON diabaikan.
    }
  };

  const connect = () => {
    if (closed || attempt >= MAX_ATTEMPTS) return;

    source = new EventSource(`${API_BASE}/jobs/${jobId}/stream`);
    source.onopen = () => {
      // Koneksi sehat: reset backoff supaya gangguan sesaat tidak menumpuk.
      attempt = 0;
    };
    source.addEventListener("status", handle);
    source.addEventListener("end", (message) => {
      handle(message);
      closed = true;
      source?.close();
    });
    source.onerror = (event) => {
      if (closed) return;
      onError?.(event);
      source?.close();
      attempt += 1;
      timer = setTimeout(connect, Math.min(1000 * 2 ** attempt, MAX_DELAY_MS));
    };
  };

  connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    source?.close();
  };
}
