// Unggahan berkas lokal per potongan ke API, dengan resume. TANPA React agar unit-testable.
// PRD FR-1.2 (≤ 3 GB, resume bila koneksi/aplikasi terputus).
//
// KONTRAK API (apps/api/app/api/v1/uploads.py; dikunci test_frontend_contract.py):
//
//   1. POST /jobs                          {source_type: "upload", clip_count}
//   2. POST /uploads/init                  {job_id, filename, size_bytes}
//                                          -> {upload_id, part_size_bytes, part_count, received_parts}
//   3. PUT  /uploads/{upload_id}/parts/{n} badan = byte potongan ke-n (1-based)
//   4. POST /uploads/{upload_id}/complete  -> server menggabungkan lalu memulai ingest
//      DELETE /uploads/{upload_id}         -> batal (job ikut dibatalkan)
//
// Resume: id job disimpan di IndexedDB per berkas (nama+ukuran+waktu ubah).
// Memilih berkas yang sama lagi memakai job yang sama, dan /uploads/init
// mengembalikan potongan yang sudah diterima server — hanya sisanya dikirim.

export const PART_SIZE = 10 * 1024 * 1024; // bawaan server (UPLOAD_PART_BYTES)
export const MAX_FILE_SIZE = 3 * 1024 * 1024 * 1024; // 3 GB, PRD FR-1.2
const ACCEPTED_EXT = [".mp4", ".mov", ".mkv"];
const DB_NAME = "clipper-uploads";
const STORE = "resume";
const MAX_PART_ATTEMPTS = 4;

export interface PartPlan {
  partNumber: number;
  start: number;
  end: number;
}

interface InitResponse {
  upload_id: string;
  part_size_bytes: number;
  part_count: number;
  received_parts: number[];
}

// Validasi sisi klien SEBELUM upload (PRD FR-1.2). null = lolos.
export function validateFile(fileName: string, fileSize: number): string | null {
  const lower = fileName.toLowerCase();
  if (!ACCEPTED_EXT.some((ext) => lower.endsWith(ext))) {
    return `Format tidak didukung. Gunakan ${ACCEPTED_EXT.join(", ")}.`;
  }
  if (fileSize <= 0) {
    return "Berkas kosong.";
  }
  if (fileSize > MAX_FILE_SIZE) {
    const gb = (fileSize / 1024 ** 3).toFixed(2);
    return `Ukuran ${gb} GB melebihi batas 3 GB.`;
  }
  return null;
}

// Fungsi murni pembagi potongan. 3 GB / 10 MB = 307,2 -> 308 potongan.
export function chunkPlan(fileSize: number, partSize: number = PART_SIZE): PartPlan[] {
  if (fileSize <= 0) throw new Error("ukuran berkas harus positif");
  if (partSize <= 0) throw new Error("ukuran part harus positif");

  const plan: PartPlan[] = [];
  for (let offset = 0, partNumber = 1; offset < fileSize; partNumber += 1) {
    const end = Math.min(offset + partSize, fileSize);
    plan.push({ partNumber, start: offset, end });
    offset = end;
  }
  return plan;
}

// Backoff eksponensial murni (testable): 1s, 2s, 4s, 8s (maks) + jitter.
export function backoffDelay(attempt: number): number {
  const base = Math.min(1000 * 2 ** Math.max(0, attempt - 1), 8000);
  return base + Math.floor(Math.random() * 250);
}

export function resumeKey(fileName: string, fileSize: number, lastModified: number): string {
  return `${fileName}:${fileSize}:${lastModified}`;
}

// --- IndexedDB minimal: kunci berkas -> id job ---

interface ResumeState {
  key: string;
  jobId: string;
}

function openDb(): Promise<IDBDatabase> {
  const { promise, resolve, reject } = Promise.withResolvers<IDBDatabase>();
  const request = indexedDB.open(DB_NAME, 1);
  request.onupgradeneeded = () => {
    if (!request.result.objectStoreNames.contains(STORE)) {
      request.result.createObjectStore(STORE, { keyPath: "key" });
    }
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
  return promise;
}

async function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    const { promise, resolve, reject } = Promise.withResolvers<T>();
    const request = fn(db.transaction(STORE, mode).objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    return await promise;
  } finally {
    db.close();
  }
}

// IndexedDB bisa tidak tersedia (mode privat, storage diblokir). Resume adalah
// kemudahan, bukan keharusan — kegagalannya tidak boleh menggagalkan upload.
export async function loadResumeJob(key: string): Promise<string | null> {
  try {
    return (await tx<ResumeState | undefined>("readonly", (s) => s.get(key)))?.jobId ?? null;
  } catch {
    return null;
  }
}

async function saveResumeJob(key: string, jobId: string): Promise<void> {
  try {
    await tx("readwrite", (s) => s.put({ key, jobId } satisfies ResumeState));
  } catch {
    // diabaikan
  }
}

export async function clearResume(key: string): Promise<void> {
  try {
    await tx("readwrite", (s) => s.delete(key));
  } catch {
    // diabaikan
  }
}

// --- Orkestrasi ---

export interface UploadCallbacks {
  onProgress?: (uploadedBytes: number, totalBytes: number) => void;
  onStage?: (stage: string) => void;
  /** Id job & upload diketahui — dipakai tombol Batal untuk membersihkan server. */
  onUploadReady?: (ids: { jobId: string; uploadId: string }) => void;
  /** Jumlah klip yang ingin dibuat (1–30, bawaan 5); hanya dipakai saat job baru dibuat. */
  clipCount?: number;
}

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // bukan JSON
  }
  return `${fallback} (HTTP ${response.status})`;
}

export async function uploadMultipart(
  file: File,
  apiBase: string,
  signal?: AbortSignal,
  cb: UploadCallbacks = {},
): Promise<{ jobId: string; uploadId: string }> {
  const clientError = validateFile(file.name, file.size);
  if (clientError) throw new Error(clientError);

  const send = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
    const jsonBody = typeof init.body === "string";
    const response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: jsonBody ? { "content-type": "application/json" } : undefined,
      signal,
    });
    if (!response.ok) {
      throw new HttpError(response.status, await errorMessage(response, `Permintaan ke ${path} gagal`));
    }
    return (response.status === 204 ? undefined : await response.json()) as T;
  };

  const key = resumeKey(file.name, file.size, file.lastModified);
  const initUpload = (jobId: string) =>
    send<InitResponse>("/uploads/init", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, filename: file.name, size_bytes: file.size }),
    });

  cb.onStage?.("menyiapkan job");
  let jobId = await loadResumeJob(key);
  let init: InitResponse | null = null;
  if (jobId) {
    try {
      init = await initUpload(jobId);
    } catch (error) {
      // Job lama sudah dihapus/diproses: mulai dari job baru.
      if (!(error instanceof HttpError) || ![404, 409].includes(error.status)) throw error;
      await clearResume(key);
      jobId = null;
    }
  }
  if (!jobId || !init) {
    jobId = (
      await send<{ id: string }>("/jobs", {
        method: "POST",
        body: JSON.stringify({ source_type: "upload", clip_count: cb.clipCount ?? 5 }),
      })
    ).id;
    await saveResumeJob(key, jobId);
    init = await initUpload(jobId);
  }
  const { upload_id: uploadId } = init;
  cb.onUploadReady?.({ jobId, uploadId });

  const plan = chunkPlan(file.size, init.part_size_bytes);
  const received = new Set(init.received_parts);
  // Byte yang sudah diterima dihitung agar progres tidak melompat ke 0 saat resume.
  let uploadedBytes = plan.filter((p) => received.has(p.partNumber)).reduce((sum, p) => sum + p.end - p.start, 0);
  cb.onProgress?.(uploadedBytes, file.size);
  cb.onStage?.(received.size ? `melanjutkan (${received.size}/${plan.length} bagian tersimpan)` : "mengunggah");

  for (const part of plan) {
    if (received.has(part.partNumber)) continue;
    const blob = file.slice(part.start, part.end);
    for (let attempt = 1; ; attempt += 1) {
      try {
        await send(`/uploads/${uploadId}/parts/${part.partNumber}`, { method: "PUT", body: blob });
        break;
      } catch (error) {
        if ((error as DOMException)?.name === "AbortError") throw error;
        if (attempt >= MAX_PART_ATTEMPTS) {
          throw new Error(
            `Bagian ${part.partNumber} gagal setelah ${MAX_PART_ATTEMPTS} percobaan: ${
              error instanceof Error ? error.message : String(error)
            }`,
          );
        }
        await sleep(backoffDelay(attempt));
      }
    }
    uploadedBytes += part.end - part.start;
    cb.onProgress?.(uploadedBytes, file.size);
  }

  cb.onStage?.("menggabungkan berkas");
  await send(`/uploads/${uploadId}/complete`, { method: "POST" });
  await clearResume(key);
  cb.onStage?.("selesai");
  return { jobId, uploadId };
}

/** Batalkan unggahan di server (upaya terbaik) dan lupakan status resume berkas. */
export async function abortRemoteUpload(
  apiBase: string,
  uploadId: string,
  resumeKeyValue?: string,
  fetchFn: typeof fetch = fetch,
): Promise<void> {
  await fetchFn(`${apiBase}/uploads/${uploadId}`, { method: "DELETE" }).catch(() => undefined);
  if (resumeKeyValue) await clearResume(resumeKeyValue);
}
