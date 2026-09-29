"use client";
// Uploader drag-and-drop. PRD FR-1.2.
// Logika unggah + resume ada di lib/upload/multipart.ts (tanpa React).
// Gaya: Neo-Brutalism — area jatuhkan bergaris tebal + shadow keras.
import { useCallback, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import Link from "next/link";
import { api } from "@/lib/api/client";
import {
  abortRemoteUpload,
  resumeKey,
  uploadMultipart,
  validateFile,
} from "@/lib/upload/multipart";
import ClipCountPicker from "@/components/ClipCountPicker";
import LanguagePicker from "@/components/LanguagePicker";
import type { JobLanguage } from "@/lib/types";

/**
 * Durasi video lokal lewat elemen `<video>` bawaan browser (hanya metadata,
 * berkas tidak dibaca utuh). `null` bila kodek/kontainer tidak dikenali browser
 * (sebagian .mkv) — batas jumlah klip lalu ditegakkan backend saat analisis.
 */
function readVideoDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => done(Number.isFinite(video.duration) ? video.duration : null);
    video.onerror = () => done(null);
    video.src = url;
  });
}

export default function Uploader() {
  const [file, setFile] = useState<File | null>(null);
  const [clipCount, setClipCount] = useState(5);
  const [language, setLanguage] = useState<JobLanguage>("id");
  /** Durasi berkas terpilih (detik), dibaca browser; `null` bila tidak terbaca. */
  const [durationS, setDurationS] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pct, setPct] = useState(0);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [doneJob, setDoneJob] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  // Diisi begitu server membuat upload (sebelum potongan pertama dikirim),
  // supaya Batal di tengah unggahan benar-benar membersihkan server.
  const uploadIdRef = useRef<string | null>(null);

  const onDrop = useCallback((accepted: File[]) => {
    const f = accepted[0];
    if (!f) return;
    const err = validateFile(f.name, f.size); // guard SEBELUM upload
    setError(err);
    setFile(err ? null : f);
    setDurationS(null);
    if (!err) void readVideoDuration(f).then(setDurationS);
    setPct(0);
    setStage("");
    setDoneJob(null);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    multiple: false,
    // react-dropzone hanya filter dialog; validateFile() yang mengikat.
    accept: { "video/mp4": [".mp4"], "video/quicktime": [".mov"], "video/x-matroska": [".mkv"] },
  });

  const start = async () => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    const c = new AbortController();
    ctrl.current = c;
    try {
      const { jobId } = await uploadMultipart(file, api.base, c.signal, {
        onProgress: (done, total) => setPct(total ? Math.round((done / total) * 100) : 0),
        onStage: setStage,
        onUploadReady: ({ uploadId }) => {
          uploadIdRef.current = uploadId;
        },
        clipCount,
        language,
      });
      uploadIdRef.current = null;
      setDoneJob(jobId);
    } catch (e) {
      if ((e as DOMException)?.name === "AbortError") setError("Upload dijeda/dibatalkan. Mulai lagi untuk lanjut dari bagian tersimpan.");
      else setError(e instanceof Error ? e.message : "Upload gagal.");
    } finally {
      setBusy(false);
      setStage("");
      ctrl.current = null;
    }
  };

  const pause = () => ctrl.current?.abort();

  const cancel = async () => {
    ctrl.current?.abort();
    if (uploadIdRef.current && file) {
      await abortRemoteUpload(api.base, uploadIdRef.current, resumeKey(file.name, file.size, file.lastModified));
      uploadIdRef.current = null;
    }
    setBusy(false);
    setPct(0);
    setStage("");
    setError(null);
  };

  return (
    <div>
      <div
        {...getRootProps()}
        className="panel"
        style={{
          padding: "2.5rem 1.5rem",
          textAlign: "center",
          cursor: "pointer",
          // Saat berkas diseret: blok lime + shadow lebih besar — umpan balik
          // jelas tanpa menggeser tata letak (hindari layout shift).
          background: isDragActive ? "var(--toxic)" : "var(--panel)",
          boxShadow: isDragActive ? "var(--shadow-lg)" : "var(--shadow)",
          borderStyle: isDragActive ? "dashed" : "solid",
        }}
      >
        <input {...getInputProps()} />
        <svg
          aria-hidden="true"
          width="40"
          height="40"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="square"
          style={{ display: "block", margin: "0 auto 0.75rem" }}
        >
          <path d="M12 3v12M6.5 9.5 12 15l5.5-5.5M4 20h16" />
        </svg>
        {isDragActive ? (
          <p className="label" style={{ margin: 0, fontSize: "0.9rem" }}>
            Lepaskan berkas di sini
          </p>
        ) : (
          <>
            <p style={{ fontWeight: 900, fontSize: "1.05rem", margin: "0 0 0.35rem" }}>
              Seret &amp; letakkan video
            </p>
            <p className="muted" style={{ fontSize: "0.88rem", margin: 0 }}>
              atau klik untuk memilih — .mp4 / .mov / .mkv, maks 3 GB
            </p>
          </>
        )}
      </div>

      {file ? (
        <div
          className="panel"
          style={{
            marginTop: "1rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <span className="mono" style={{ fontSize: "0.8rem", fontWeight: 900 }}>
            {file.name}
          </span>
          <span className="badge badge-queued">
            {(file.size / 1e9).toFixed(2)} GB
          </span>
        </div>
      ) : null}

      {error ? (
        <p className="alert" role="alert" style={{ marginTop: "1rem", marginBottom: 0 }}>
          {error}
        </p>
      ) : null}

      {/* Jumlah klip dan bahasa dipilih SEBELUM menekan Upload: job dibuat di
          dalam uploadMultipart(), jadi nilainya harus sudah final saat itu. */}
      <div style={{ marginTop: "1rem" }}>
        <LanguagePicker id="up-lang" value={language} onChange={setLanguage} disabled={busy} />
        <ClipCountPicker
          id="up-clips"
          value={clipCount}
          onChange={setClipCount}
          durationS={durationS}
          disabled={busy}
        />
      </div>

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={start} disabled={!file || busy}>
          {busy ? "Mengunggah…" : "Upload"}
        </button>
        <button className="btn" onClick={pause} disabled={!busy}>
          Jeda
        </button>
        <button className="btn" onClick={cancel} disabled={!busy && !file}>
          Batal
        </button>
      </div>

      {/* Progres */}
      <div
        style={{ marginTop: "1rem" }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Progres unggah"
      >
        <div className="progress" style={{ height: 22 }}>
          <div className="progress-fill" style={{ "--pct": pct } as React.CSSProperties} />
        </div>
        <div
          className="mono"
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: "0.72rem",
            marginTop: "0.4rem",
          }}
        >
          <span>{pct}%</span>
          <span className="muted">
            {busy ? stage || "berjalan" : doneJob ? "selesai" : "siaga"}
          </span>
        </div>
      </div>

      {doneJob ? (
        <p className="alert alert-ok" role="status" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
          <span className="label">Selesai — job dibuat</span>
          <br />
          <Link href={`/jobs/${doneJob}`} className="mono" style={{ fontWeight: 900 }}>
            {doneJob}
          </Link>
        </p>
      ) : null}
    </div>
  );
}