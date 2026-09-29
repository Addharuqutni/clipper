// Tipe cermin respons API. Komentar Indonesia, identifier Inggris.

/** Nilai jobs.stage yang ditulis API/worker. */
export type JobStage = "upload" | "ingest" | "transcribe" | "analyze" | "render" | "done";
/** Nilai jobs.status (CHECK constraint di basis data). */
export type JobStatus = "queued" | "running" | "done" | "failed" | "canceled";
export type SourceType = "upload" | "youtube";
export type SegmentStatus = "proposed" | "selected" | "rejected";
export type RenderKind = "preview" | "final";
/** Bahasa ucapan yang dipilih saat membuat job; `auto` = deteksi otomatis. */
export type JobLanguage = "id" | "en" | "auto";

export interface Job {
  id: string; user_id: string; source_type: SourceType;
  source_url: string | null; status: JobStatus; stage: JobStage | null;
  progress: number;
  /** Jumlah klip yang diminta pengguna (1–30, dibatasi durasi video saat analisis). */
  clip_count: number;
  /** Bahasa ucapan yang dipilih; `auto` = deteksi otomatis. */
  language: JobLanguage;
  error: string | null;
  created_at: string; updated_at: string;
}

/**
 * Halaman daftar job. Bentuknya mengikuti ``JobListResponse`` di backend —
 * bukan array telanjang.
 */
export interface JobListResponse {
  items: Job[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Satu kata dari endpoint transcript (`GET /jobs/{id}/transcript`).
 *
 * `index` dipakai saat memperbarui satu kata; posisi array bisa bergeser
 * setelah penghapusan.
 */
export interface TranscriptWord {
  index: number;
  text: string;
  start_s: number;
  end_s: number;
}

/** Transkrip per kata untuk editor. `note` menjelaskan bila `words` kosong. */
export interface TranscriptResponse {
  job_id: string;
  language: string | null;
  model_used: string | null;
  full_text: string | null;
  words: TranscriptWord[];
  note: string;
}

/** Hasil satu perubahan kata. `removed` true bila kata dihapus. */
export interface WordUpdateResponse {
  job_id: string;
  index: number;
  removed: boolean;
  word_count: number;
}

/** Kolom skor boleh `null` untuk segmen yang belum dinilai AI. */
export interface Segment {
  id: string; start_s: number; end_s: number;
  score: number | null; label: string | null; hook_score: number | null;
  completeness: number | null; emotional_arc: number | null;
  reason: string | null; status: SegmentStatus;
}

/** Caption + hashtag AI untuk unggahan klip (FR-4.2). */
export interface SocialCaption {
  caption: string;
  hashtags: string[];
}
export interface Render {
  id: string; segment_id: string; kind: RenderKind; r2_key: string | null;
  preset: string | null; status: "queued" | "running" | "done" | "failed" | "canceled";
  duration_ms: number | null; size_bytes: number | null; created_at: string;
  /** Mode reframing yang dipakai render ini (migrasi 0002). */
  crop_mode: CropMode;
}
/**
 * Daftar segmen untuk satu job. `note` menjelaskan MENGAPA kosong — job masih
 * diproses, atau analisis selesai tanpa hasil. Keduanya butuh tindakan berbeda.
 */
export interface SegmentListResponse {
  items: Segment[];
  total: number;
  note: string;
}

/**
 * Metadata media sumber. Dipakai untuk menghitung geometri crop secara akurat.
 * Backend mengembalikan `null` bila metadata belum tersedia (ingest belum jalan).
 */
export interface MediaInfo {
  width: number;
  height: number;
  duration_s: number | null;
  codec: string | null;
  size_bytes: number | null;
}

/** Event SSE progres job (`event: status` / `event: end`). */
export interface JobEvent {
  job_id: string; status: JobStatus; stage: JobStage | null;
  progress: number; message: string | null;
}

// ---------------------------------------------------------------------------
// Fitur reframing (mode crop)
// ---------------------------------------------------------------------------

/** Mode penyusunan klip vertikal. Harus sama persis dengan CropMode backend. */
export type CropMode = "face_track" | "black_bars" | "blurred_fill";

export interface CropModeInfo {
  id: CropMode;
  label: string;
  description: string;
  /** Mode ini memakai deteksi wajah — jauh lebih berat di CPU tanpa GPU. */
  requires_face_detection: boolean;
  /** Video tampil utuh tanpa bagian terpotong (mode berbar). */
  preserves_full_frame: boolean;
}

export interface LetterboxPreview {
  mode: CropMode;
  source_width: number; source_height: number;
  output_width: number; output_height: number;
  scaled_width: number; scaled_height: number;
  bar_height_top: number; bar_height_bottom: number;
  bar_width_left: number; bar_width_right: number;
  /** Proporsi tinggi layar yang tertutup bar (0 untuk face tracking). */
  bar_ratio: number;
  filter_chain: string;
  warning: string;
}

// ---------------------------------------------------------------------------
// Penyedia AI kustom
// ---------------------------------------------------------------------------

export interface AiProviderInfo {
  id: string;
  label: string;
  default_model: string;
  base_url: string;
  requires_api_key: boolean;
}

export interface AiProviderTestResult {
  ok: boolean;
  message: string;
  resolved_base_url: string;
  resolved_model: string;
  latency_ms: number | null;
  /** Cuplikan jawaban model — bukti bahwa endpoint benar-benar hidup. */
  sample: string;
  /** Saran tindak lanjut saat gagal. */
  hint: string;
}

/**
 * Pengaturan penyedia AI yang tersimpan.
 *
 * Tidak ada field `api_key` di sini dengan sengaja — server tidak pernah
 * mengembalikan nilainya. `has_api_key` memberi tahu UI apakah kunci sudah
 * diisi tanpa membocorkan isinya.
 */
export interface SavedAiSettings {
  preset: string;
  base_url: string | null;
  model: string | null;
  has_api_key: boolean;
  allow_private_host: boolean;
  default_direction: string | null;
  /** Konteks model (token); `null` = penyedia tidak melaporkan, dipakai cadangan. */
  context_tokens: number | null;
  /** Durasi video maksimum menurut konteks model, dibatasi MAX_VIDEO_DURATION_MIN. */
  max_video_minutes: number;
  updated_at: string | null;
}

export interface SaveAiSettingsPayload {
  preset: string;
  base_url?: string | null;
  model?: string | null;
  api_key?: string;
  clear_api_key?: boolean;
  allow_private_host?: boolean;
  default_direction?: string | null;
  context_tokens?: number | null;
}

// ---------------------------------------------------------------------------
// YouTube cookies
// ---------------------------------------------------------------------------

export interface CookieValidationResult {
  valid: boolean;
  /** Nama cookie yang ditemukan — TIDAK PERNAH berisi nilainya. */
  found: string[];
  missing: string[];
  message: string;
  warning: string;
  /** True bila cookies sah dan tersimpan untuk unduhan berikutnya. */
  stored: boolean;
}

export interface CookieRequirements {
  required_any_of: string[];
  explanation: string;
  max_file_bytes: number;
}

// ---------------------------------------------------------------------------
// Gaya teks / caption (Pilar 2 — Kustomisasi Teks)
// ---------------------------------------------------------------------------

/** Jenis animasi teks yang didukung backend. */
export type CaptionAnimation = "none" | "fade" | "bounce" | "pop" | "karaoke";

/**
 * Gaya teks yang dapat disetel pengguna.
 *
 * Bentuknya mencerminkan `SubtitleStyle` di `clipper_shared.subtitles`. Nilai
 * waktu render ditulis untuk kanvas acuan 1080x1920, lalu diskalakan otomatis
 * ke resolusi nyata klip.
 */
export interface CaptionStyle {
  font_name: string;
  font_size: number;
  primary_rgb: string;
  highlight_rgb: string;
  outline_rgb: string;
  back_rgb: string;
  back_alpha: number;
  outline: number;
  shadow: number;
  margin_side: number;
  margin_vertical: number;
  border_style: number;
  alignment: number;
  bold: boolean;
  italic: boolean;
  letter_spacing: number;
  animation: CaptionAnimation;
  animation_ms: number;
  animation_scale: number;
  chunk_size: number;
}

/** Preset gaya teks milik pengguna. */
export interface CaptionPreset {
  id: string;
  name: string;
  style: CaptionStyle;
  /** True bila preset ini dipakai saat job tidak punya override. */
  is_default: boolean;
}

export interface CaptionPresetList {
  items: CaptionPreset[];
  total: number;
}

/** Pilihan gaya dari server, agar UI tidak menyalin daftar secara manual. */
export interface CaptionStyleOptions {
  animations: CaptionAnimation[];
  defaults: CaptionStyle;
  font_size_min: number;
  font_size_max: number;
  alignment_min: number;
  alignment_max: number;
  border_styles: number[];
  max_presets: number;
}

/** Gaya efektif sebuah job beserta asalnya. */
export interface JobCaptionStyle {
  job_id: string;
  style: CaptionStyle;
  /** `job` = khusus video ini, `preset` = preset bawaan, `default` = bawaan sistem. */
  source: "job" | "preset" | "default";
  preset_name: string | null;
}

/** Font kustom yang diunggah pengguna. */
export interface FontAsset {
  id: string;
  /** Nama keluarga font seperti yang dikenal libass. */
  family: string;
  original_filename: string;
  size_bytes: number;
  r2_key: string;
}

export interface FontAssetList {
  items: FontAsset[];
  total: number;
}

// ---------------------------------------------------------------------------
// Overlay B-roll / efek suara (Pilar 3)
// ---------------------------------------------------------------------------

/** Jenis aset overlay. `audio` tidak ikut compositing gambar. */
export type OverlayKind = "image" | "video" | "audio";

/** Penempatan overlay di dalam frame. */
export type OverlayPosition =
  | "top_left" | "top_right" | "bottom_left" | "bottom_right"
  | "center" | "top_center" | "bottom_center" | "full";

export type OverlayTransition = "none" | "fade" | "slide";

/** Satu aset B-roll/efek suara di pustaka pengguna. */
export interface OverlayAsset {
  id: string;
  kind: OverlayKind;
  name: string;
  /** Kata kunci pemicu usulan otomatis. */
  tags: string[];
  size_bytes: number;
  duration_s: number | null;
  default_position: OverlayPosition;
  default_scale: number;
}

export interface OverlayAssetList {
  items: OverlayAsset[];
  total: number;
}

/** Satu penempatan overlay pada segmen. */
export interface OverlayPlacement {
  id: string;
  segment_id: string;
  asset_id: string;
  asset_name: string;
  asset_kind: OverlayKind;
  start_s: number;
  end_s: number;
  position: OverlayPosition;
  scale: number;
  opacity: number;
  transition: OverlayTransition;
  transition_ms: number;
  /** True bila ini usulan otomatis yang belum ditinjau pengguna. */
  suggested: boolean;
}

export interface OverlayPlacementList {
  items: OverlayPlacement[];
  total: number;
}

/** Pilihan overlay dari server. */
export interface OverlayOptions {
  kinds: OverlayKind[];
  positions: OverlayPosition[];
  transitions: OverlayTransition[];
  max_bytes: number;
}

export interface OverlaySuggestResult {
  segment_id: string;
  created: OverlayPlacement[];
  matched_tags: string[];
}

// ---------------------------------------------------------------------------
// Log pemrosesan job
// ---------------------------------------------------------------------------

/**
 * Satu baris log tahapan job.
 *
 * Berbeda dari `JobEvent` (yang dipakai SSE dan memuat `payload`), bentuk ini
 * datang dari endpoint riwayat `GET /jobs/{id}/events` dan membawa `elapsed_s`
 * yang dihitung server. Sengaja tidak menyatukan keduanya: SSE dan riwayat
 * punya kebutuhan berbeda, dan memaksakan satu tipe akan membuat salah satunya
 * penuh field opsional.
 */
export interface JobLogEntry {
  id: string;
  stage: string;
  message: string | null;
  /** Detik sejak event pertama. Baris pertama selalu 0. */
  elapsed_s: number;
  created_at: string;
}

export interface JobLogList {
  items: JobLogEntry[];
  total: number;
  /** Menjelaskan MENGAPA kosong — belum diproses, atau log hilang. */
  note: string;
}
