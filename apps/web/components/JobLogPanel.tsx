"use client";

// Panel log pemrosesan job.
//
// **Mengapa log, bukan sekadar progress bar.** Progress bar menjawab "berapa
// persen", tetapi tidak "kenapa berhenti". Ketika job gagal di tengah atau
// tampak tidak bergerak, satu-satunya cara mengetahui sebabnya adalah melihat
// urutan tahapan yang benar-benar dilalui — dan itulah yang ditampilkan di sini.
//
// **Poin desain yang disengaja:** waktu ditampilkan sebagai SELISIH dari event
// pertama, bukan jam absolut. Saat menelusuri proses yang berjalan menit-menitan,
// yang ingin diketahui adalah "tahap mana yang lambat", bukan "jam berapa".
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api/client";
import type { JobLogEntry } from "@/lib/types";

interface Props {
  jobId: string;
  /** Status job; dipakai untuk memutuskan apakah perlu memantau berkala. */
  status?: string;
}

/** Warna per tahap, supaya mata bisa memindai tanpa membaca setiap baris. */
const STAGE_COLOR: Record<string, string> = {
  ingest: "#9ad0ff",
  transcribe: "#c9a6ff",
  analyze: "var(--accent)",
  render: "var(--toxic)",
  done: "var(--toxic)",
  failed: "#ffb3b3",
};

/** Ubah detik menjadi bentuk yang mudah dibaca ("1m 12s"). */
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export default function JobLogPanel({ jobId, status }: Props) {
  const [entries, setEntries] = useState<JobLogEntry[] | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.getJobLog(jobId);
      setEntries(data.items);
      setNote(data.note);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat log.");
      setEntries([]);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Pantau berkala HANYA saat job masih berjalan. Log job yang sudah selesai
  // tidak berubah, jadi memintanya ulang setiap beberapa detik hanya membebani
  // server tanpa manfaat.
  const isLive = status === "running" || status === "queued";
  useEffect(() => {
    if (!autoRefresh || !isLive) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, isLive, load]);

  const visible = useMemo(() => {
    if (!entries) return [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter(
      (e) =>
        e.stage.toLowerCase().includes(needle) ||
        (e.message ?? "").toLowerCase().includes(needle),
    );
  }, [entries, filter]);

  if (error) {
    return (
      <div className="panel alert" role="alert">
        {error}
      </div>
    );
  }

  if (entries === null) {
    return (
      <div className="panel" role="status">
        <span className="label muted">Memuat log…</span>
      </div>
    );
  }

  return (
    <div className="panel">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "0.5rem",
          flexWrap: "wrap",
        }}
      >
        <span className="label">
          Log pemrosesan {entries.length ? `(${entries.length})` : ""}
        </span>
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          {isLive ? (
            <button
              className="btn"
              type="button"
              onClick={() => setAutoRefresh((v) => !v)}
              style={{ fontSize: "0.62rem", padding: "0.2rem 0.5rem" }}
              title="Muat ulang otomatis setiap 5 detik selama job berjalan"
            >
              {autoRefresh ? "Pantau: aktif" : "Pantau: mati"}
            </button>
          ) : null}
          <button
            className="btn"
            type="button"
            onClick={() => void load()}
            style={{ fontSize: "0.62rem", padding: "0.2rem 0.5rem" }}
          >
            Muat ulang
          </button>
        </div>
      </div>

      {entries.length ? (
        <input
          className="input"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Cari tahap atau pesan…"
          aria-label="Cari dalam log"
          style={{ marginTop: "0.6rem", fontSize: "0.75rem" }}
        />
      ) : null}

      {!entries.length ? (
        <p className="muted" style={{ fontSize: "0.8rem", marginTop: "0.6rem", marginBottom: 0 }}>
          {note || "Belum ada log untuk job ini."}
        </p>
      ) : (
        <div
          style={{
            marginTop: "0.6rem",
            maxHeight: 340,
            overflowY: "auto",
            border: "var(--bw) solid var(--line)",
            background: "#0f0f0f",
            padding: "0.5rem 0.6rem",
          }}
          role="log"
          aria-label="Log pemrosesan"
        >
          {visible.map((entry, index) => (
            <div
              key={entry.id}
              style={{
                display: "flex",
                gap: "0.55rem",
                alignItems: "baseline",
                // Tepi tipis memisahkan baris tanpa menambah tinggi yang berarti.
                borderBottom:
                  index < visible.length - 1 ? "1px solid rgba(255,255,255,0.07)" : "none",
                padding: "0.22rem 0",
              }}
            >
              <span
                className="mono"
                style={{
                  fontSize: "0.62rem",
                  color: "#8a8a8a",
                  minWidth: "4.2rem",
                  textAlign: "right",
                  flexShrink: 0,
                }}
                title={`${entry.elapsed_s.toFixed(2)} detik sejak log pertama`}
              >
                +{formatElapsed(entry.elapsed_s)}
              </span>
              <span
                className="mono"
                style={{
                  fontSize: "0.6rem",
                  color: STAGE_COLOR[entry.stage] ?? "#dcdcdc",
                  minWidth: "4.6rem",
                  flexShrink: 0,
                  fontWeight: 700,
                }}
              >
                {entry.stage}
              </span>
              <span
                className="mono"
                style={{
                  fontSize: "0.68rem",
                  color: "#e8e8e8",
                  // Pesan boleh membungkus: memotongnya dengan ellipsis akan
                  // menyembunyikan justru bagian yang menjelaskan kegagalan.
                  wordBreak: "break-word",
                }}
              >
                {entry.message || "—"}
              </span>
            </div>
          ))}
          {!visible.length ? (
            <p className="mono" style={{ fontSize: "0.68rem", color: "#8a8a8a", margin: 0 }}>
              Tidak ada baris yang cocok dengan &quot;{filter}&quot;.
            </p>
          ) : null}
        </div>
      )}

      <p className="muted" style={{ fontSize: "0.66rem", marginTop: "0.45rem", marginBottom: 0 }}>
        Waktu dihitung sebagai selisih dari baris pertama, bukan jam absolut —
        supaya durasi tiap tahap langsung terbaca.
      </p>
    </div>
  );
}
