"use client";

// Dashboard — data NYATA dari API.
//
// Sebelumnya halaman ini memakai array `MOCK` yang tertanam di kode dan tidak
// pernah memanggil backend. Akibatnya dashboard selalu menampilkan empat job
// palsu walau basis data kosong — pengguna mengira ada pekerjaan berjalan
// padahal tidak ada apa pun. Data contoh yang tidak bisa dibedakan dari data
// asli lebih buruk daripada tidak ada data sama sekali.
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api/client";
import type { Job } from "@/lib/types";

/** Kelas badge per status. Warna TIDAK pernah jadi satu-satunya penanda —
 *  teks status selalu ikut ditampilkan. */
function badgeClass(status: string): string {
  return `badge badge-${status}`;
}

/** Interval polling selama ada job yang diproses (ms). */
const POLL_MS = 5000;

/** Label status dalam bahasa pengguna, bukan nama internal. */
const STATUS_LABEL: Record<string, string> = {
  queued: "Antre",
  running: "Diproses",
  done: "Selesai",
  failed: "Gagal",
  canceled: "Dibatalkan",
};

const STAGE_LABEL: Record<string, string> = {
  upload: "Menunggu unggahan",
  ingest: "Mengambil video",
  transcribe: "Transkripsi",
  analyze: "Memilih momen",
  render: "Merender klip",
  done: "Selesai",
};

function StatBlock({ label, value, bg }: { label: string; value: number; bg: string }) {
  return (
    <div className="panel" style={{ background: bg, padding: "1rem" }}>
      <div className="label" style={{ color: "var(--ink)", opacity: 0.75 }}>
        {label}
      </div>
      <div style={{ fontSize: "2.5rem", fontWeight: 900, lineHeight: 1 }}>{value}</div>
    </div>
  );
}

/** Kerangka tabel saat memuat — mencegah tata letak melompat saat data tiba. */
function TableSkeleton() {
  return (
    <div className="panel" aria-busy="true" aria-live="polite">
      <span className="label muted">Memuat job…</span>
      {[0, 1, 2].map((row) => (
        <div key={row} className="skeleton" style={{ height: 38, marginTop: "0.6rem" }} />
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      // `listJobs` bertipe `JobListResponse`, jadi bentuknya sudah pasti dan
      // tidak perlu pemeriksaan bentuk manual di sini.
      const data = await api.listJobs();
      setJobs(data.items);
      setError(null);
    } catch (e: unknown) {
      setError(
        e instanceof ApiError
          ? e.detail
          : "Tidak dapat memuat daftar job. Pastikan backend berjalan.",
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Muat ulang otomatis saat tab kembali aktif: job yang diproses di latar
  // belakang akan terlihat tanpa perlu menekan tombol.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  const list = jobs ?? [];
  const active = list.filter((j) => j.status === "running" || (j.status === "queued" && j.stage !== "upload")).length;

  // Selama ada job diproses, muat ulang berkala supaya progres bergerak sendiri.
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [active, load]);

  if (jobs === null && !error) return <TableSkeleton />;

  const total = list.length;
  const done = list.filter((j) => j.status === "done").length;
  const failed = list.filter((j) => j.status === "failed").length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            className="btn"
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            aria-busy={refreshing}
          >
            {refreshing ? "Memuat…" : "Muat ulang"}
          </button>
          <Link href="/upload" className="btn btn-primary">
            Buat job baru
          </Link>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert" style={{ margin: 0 }}>
          <div>{error}</div>
          <button type="button" className="btn" onClick={() => void load()} style={{ marginTop: "0.5rem" }}>
            Coba lagi
          </button>
        </div>
      ) : null}

      <div className="grid grid-4">
        <StatBlock label="Total job" value={total} bg="var(--panel)" />
        <StatBlock label="Sedang diproses" value={active} bg="var(--blue)" />
        <StatBlock label="Selesai" value={done} bg="var(--toxic)" />
        <StatBlock label="Gagal" value={failed} bg="var(--pink)" />
      </div>

      <div style={{ minWidth: 0 }}>
          <h2>Job terbaru</h2>

          {total === 0 && !error ? (
            // Keadaan kosong yang menjelaskan langkah berikutnya, bukan layar
            // kosong tanpa arah.
            <div className="panel" style={{ textAlign: "center", padding: "2rem 1rem" }}>
              <h3 style={{ margin: 0 }}>Belum ada job</h3>
              <p className="muted" style={{ fontSize: "0.88rem", margin: "0.6rem 0 1rem" }}>
                Mulai dengan menempel tautan YouTube atau mengunggah berkas video.
              </p>
              <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center", flexWrap: "wrap" }}>
                <Link href="/youtube" className="btn btn-primary">
                  Dari YouTube
                </Link>
                <Link href="/upload" className="btn">
                  Unggah berkas
                </Link>
              </div>
            </div>
          ) : (
            <table className="table-brutal">
              <caption
                className="label muted"
                style={{ captionSide: "bottom", textAlign: "left", paddingTop: "0.6rem" }}
              >
                {total} job ditampilkan
              </caption>
              <thead>
                <tr>
                  <th scope="col">Job</th>
                  <th scope="col">Sumber</th>
                  <th scope="col">Tahap</th>
                  <th scope="col">Progres</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {list.map((job) => (
                  <tr key={job.id}>
                    <td data-label="ID">
                      <Link
                        href={`/jobs/${job.id}`}
                        className="mono"
                        title={job.id}
                      >
                        {String(job.id).slice(0, 8)}…
                      </Link>
                    </td>
                    <td data-label="Sumber">
                      <span className={`tag tag-${job.source_type}`}>{job.source_type}</span>
                    </td>
                    <td data-label="Tahap">
                      {job.status === "running" && job.stage === "render" ? (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem" }}>
                          <span className="tally" /> {STAGE_LABEL.render}
                        </span>
                      ) : (
                        STAGE_LABEL[job.stage ?? ""] ?? job.stage
                      )}
                    </td>
                    <td data-label="Progres">
                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", minWidth: 120 }}>
                        <div className="progress" style={{ flex: 1 }}>
                          <div
                            className="progress-fill"
                            style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }}
                          />
                        </div>
                        <span className="mono" style={{ fontSize: "0.75rem", minWidth: 34 }}>
                          {job.progress}%
                        </span>
                      </div>
                      {job.error ? (
                        <div className="muted" style={{ fontSize: "0.72rem", marginTop: "0.3rem" }}>
                          {job.error}
                        </div>
                      ) : null}
                    </td>
                    <td data-label="Status">
                      <span className={badgeClass(job.status)}>{STATUS_LABEL[job.status] ?? job.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </div>
    </div>
  );
}
