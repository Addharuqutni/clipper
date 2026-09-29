"use client";

// Review Studio — hasil klip sebuah job, data nyata dari API.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, api, subscribeJobStream } from "@/lib/api/client";
import type { CropMode, Job, Render, Segment, SocialCaption } from "@/lib/types";
import CropModePanel from "@/components/CropModePanel";
import TranscriptEditor from "@/components/TranscriptEditor";
import CaptionStylePanel from "@/components/CaptionStylePanel";
import OverlayTimeline from "@/components/OverlayTimeline";
import JobLogPanel from "@/components/JobLogPanel";
import ConfirmDialog, { type ConfirmDialogProps } from "@/components/ConfirmDialog";

type Tab = "editor" | "segments" | "style" | "log";
const TABS: { id: Tab; label: string }[] = [
  { id: "editor", label: "Editor teks" },
  { id: "segments", label: "Klip" },
  { id: "style", label: "Tampilan" },
  { id: "log", label: "Log proses" },
];

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "Antre",
  running: "Diproses",
  done: "Selesai",
  failed: "Gagal",
  canceled: "Dibatalkan",
};

type JobAction = "redispatch" | "rescore" | "delete";

/** Isi dialog konfirmasi per aksi. Akibatnya mengikuti perilaku backend:
 *  proses ulang mulai lagi dari ingest (transkrip + segmen diganti), analisis
 *  ulang memakai transkrip yang ada tetapi mengganti segmen, dan segmen yang
 *  diganti ikut menghapus render serta overlay-nya (ON DELETE CASCADE). */
const ACTION_CONFIRM: Record<JobAction, Omit<ConfirmDialogProps, "open" | "onConfirm" | "onCancel">> = {
  redispatch: {
    title: "Proses ulang job ini?",
    description: "Job dijalankan lagi dari awal: ambil video, transkripsi, lalu analisis AI.",
    consequences: [
      "Transkrip, termasuk koreksi teks yang sudah Anda buat, diganti hasil transkripsi baru.",
      "Semua klip, rentang yang diubah, overlay, dan hasil render diganti.",
      "Memakan waktu lebih lama dan memakai kuota penyedia AI lagi.",
    ],
    confirmLabel: "Ya, proses ulang",
    tone: "warn",
  },
  rescore: {
    title: "Analisis ulang job ini?",
    description: "AI memilih klip lagi dari transkrip yang sudah ada. Video tidak diunduh atau ditranskripsi ulang.",
    consequences: [
      "Semua klip, rentang yang diubah, overlay, dan hasil render diganti hasil analisis baru.",
      "Transkrip dan koreksi teks tetap dipertahankan.",
      "Memakai kuota penyedia AI lagi.",
    ],
    confirmLabel: "Ya, analisis ulang",
    tone: "warn",
  },
  delete: {
    title: "Hapus job ini?",
    description: "Tindakan ini tidak bisa dibatalkan.",
    consequences: [
      "Video sumber, transkrip, klip, dan semua hasil render dihapus.",
      "Salinan klip di folder output/clips tetap ada.",
    ],
    confirmLabel: "Hapus permanen",
    tone: "danger",
  },
};

function ScoreMeter({ label, value }: { label: string; value: number | null }) {
  const pct = Math.round(Math.max(0, Math.min(1, value ?? 0)) * 100);
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem" }}>
        <span className="muted">{label}</span>
        <span className="mono">{value === null ? "–" : pct}</span>
      </div>
      <div className="progress" style={{ height: 12, marginTop: 4 }}>
        <div className="progress-fill" style={{ "--pct": pct } as React.CSSProperties} />
      </div>
    </div>
  );
}

/** Detik → timecode m:ss (atau h:mm:ss untuk video panjang). */
function formatTimecode(totalS: number): string {
  const t = Math.max(0, Math.floor(totalS));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = String(t % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function SegmentTrim({ jobId, segment, onSaved }: { jobId: string; segment: Segment; onSaved: () => void }) {
  const [start, setStart] = useState(segment.start_s.toFixed(1));
  const [end, setEnd] = useState(segment.end_s.toFixed(1));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const idStart = `trim-start-${segment.id}`;
  const idEnd = `trim-end-${segment.id}`;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.updateSegment(jobId, segment.id, { start_s: Number(start), end_s: Number(end) });
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menyimpan rentang.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", flexWrap: "wrap", marginTop: "0.6rem" }}>
      <label htmlFor={idStart} className="field">
        <span className="label">Mulai (detik)</span>
        <input id={idStart} className="input" type="number" step="0.1" min="0" value={start} onChange={(e) => setStart(e.target.value)} />
      </label>
      <label htmlFor={idEnd} className="field">
        <span className="label">Selesai (detik)</span>
        <input id={idEnd} className="input" type="number" step="0.1" min="0" value={end} onChange={(e) => setEnd(e.target.value)} />
      </label>
      <button type="button" className="btn" disabled={saving || start === "" || end === ""} onClick={() => void save()}>
        {saving ? "Menyimpan…" : "Simpan rentang"}
      </button>
      {error ? <span className="alert" role="alert">{error}</span> : null}
      <span className="muted" style={{ fontSize: "0.78rem", flexBasis: "100%" }}>
        Render ulang setelah menyimpan agar klip memakai rentang baru.
      </span>
    </div>
  );
}

function SocialCaptionBox({ jobId, segmentId }: { jobId: string; segmentId: string }) {
  const [result, setResult] = useState<SocialCaption | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setResult(null);
    setError(null);
  }, [segmentId]);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.socialCaption(jobId, segmentId));
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal membuat caption.");
    } finally {
      setBusy(false);
    }
  };

  const text = result ? `${result.caption}\n\n${result.hashtags.join(" ")}`.trim() : "";
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="panel" style={{ padding: "0.85rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
        <span className="label">Caption unggahan</span>
        <button type="button" className="btn" disabled={busy} onClick={() => void generate()}>
          {busy ? "Menulis…" : result ? "Buat ulang" : "Buat dengan AI"}
        </button>
      </div>
      {error ? <p className="alert" role="alert" style={{ margin: "0.6rem 0 0" }}>{error}</p> : null}
      {result ? (
        <>
          <p style={{ margin: "0.6rem 0 0", whiteSpace: "pre-wrap" }}>{result.caption}</p>
          <p className="mono" style={{ margin: "0.4rem 0 0", fontSize: "0.8rem" }}>{result.hashtags.join(" ")}</p>
          <button type="button" className="btn" style={{ marginTop: "0.6rem" }} onClick={() => void copy()}>
            {copied ? "Tersalin" : "Salin"}
          </button>
        </>
      ) : null}
    </div>
  );
}

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const [jobId, setJobId] = useState<string>("");
  const [job, setJob] = useState<Job | null>(null);
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [renders, setRenders] = useState<Render[]>([]);
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null);
  const [renderingSegmentId, setRenderingSegmentId] = useState<string | null>(null);
  const [openOverlays, setOpenOverlays] = useState<Set<string>>(new Set());
  const [cropMode, setCropMode] = useState<CropMode | null>(null);
  const [note, setNote] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [confirming, setConfirming] = useState<JobAction | null>(null);
  // Tab awal adalah daftar klip: semua klip dirender otomatis, jadi hal
  // pertama yang dicari pengguna adalah hasil yang siap diunduh.
  const [tab, setTab] = useState<Tab>("segments");
  const loadSeq = useRef(0);

  useEffect(() => {
    // `params` adalah Promise pada Next.js 15; di-resolve sekali saat mount.
    void params.then((resolved) => setJobId(resolved.id));
  }, [params]);

  const load = useCallback(async () => {
    if (!jobId) return;
    // Hanya respons terbaru yang dipakai: event SSE beruntun memicu beberapa
    // load, dan respons lama yang tiba belakangan tidak boleh menimpa yang baru.
    const seq = ++loadSeq.current;
    try {
      const [jobData, segmentData, renderData] = await Promise.all([
        api.getJob(jobId),
        api.listSegments(jobId),
        api.listJobRenders(jobId),
      ]);
      if (seq !== loadSeq.current) return;
      setJob(jobData);
      setSegments(segmentData.items);
      setRenders(renderData.items);
      setNote(segmentData.note);
      setError(null);
      // Mode crop awal = mode render terbaru job ini (bukan selalu face_track).
      setCropMode((current) => current ?? renderData.items[0]?.crop_mode ?? "face_track");
    } catch (e: unknown) {
      if (seq !== loadSeq.current) return;
      // Data yang sudah tampil dipertahankan; gangguan sesaat cukup diberi tahu.
      setError(e instanceof ApiError ? e.detail : "Tidak dapat memuat job. Pastikan backend berjalan.");
      setSegments((current) => current ?? []);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Pesan tahap terakhir dari SSE, mis. "Transkripsi 12:30 / 40:00 · ±6 menit
  // lagi". Hanya hidup di stream (tidak disimpan di baris job), jadi muncul
  // pada event berikutnya bila halaman dibuka di tengah proses.
  const [liveMessage, setLiveMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    return subscribeJobStream(jobId, (event) => {
      setLiveMessage(event.status === "running" ? event.message : null);
      void load();
    });
  }, [jobId, load]);

  const runAction = async (action: () => Promise<unknown>, failure: string) => {
    setActionBusy(true);
    try {
      await action();
      await load();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : failure);
    } finally {
      setActionBusy(false);
    }
  };

  const handleRender = async (segmentId: string) => {
    setRenderingSegmentId(segmentId);
    try {
      await api.renderSegment(jobId, segmentId, { kind: "final", crop_mode: cropMode ?? "face_track" });
      await load();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal memulai tugas render.");
    } finally {
      setRenderingSegmentId(null);
    }
  };

  const performAction = (action: JobAction) => {
    setConfirming(null);
    if (action === "redispatch") {
      void runAction(() => api.redispatchJob(jobId), "Gagal memproses ulang.");
    } else if (action === "rescore") {
      void runAction(() => api.rescoreJob(jobId), "Gagal menjalankan analisis ulang.");
    } else {
      void runAction(async () => {
        await api.deleteJob(jobId);
        router.push("/dashboard");
      }, "Gagal menghapus job.");
    }
  };

  const onTabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = TABS.findIndex((t) => t.id === tab);
    const next = event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : null;
    if (next === null) return;
    const target = TABS[(next + TABS.length) % TABS.length];
    setTab(target.id);
    document.getElementById(`tab-${target.id}`)?.focus();
  };

  const active = job?.status === "queued" || job?.status === "running";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Hasil klip</h1>
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.4rem" }}>
            <span className="mono muted" style={{ fontSize: "0.8rem" }} title={jobId}>
              {jobId ? jobId.slice(0, 8) : "…"}
            </span>
            {job ? (
              <>
                <span className={`badge badge-${job.status}`}>
                  {job.status === "running" && job.stage === "render"
                    ? `Merender klip · ${job.progress}%`
                    : job.status === "running"
                      ? `Diproses · ${job.progress}%`
                      : job.stage === "upload" && job.status === "queued"
                        ? "Menunggu unggahan"
                        : STATUS_LABEL[job.status]}
                </span>
                <span className={`tag tag-${job.source_type}`}>{job.source_type}</span>
                {active && liveMessage ? (
                  <span className="mono muted" style={{ fontSize: "0.8rem" }} aria-live="polite">
                    {liveMessage}
                  </span>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          <Link href="/dashboard" className="btn">
            Dashboard
          </Link>
          {active ? (
            <button className="btn" type="button" disabled={actionBusy} onClick={() => void runAction(() => api.cancelJob(jobId), "Gagal membatalkan job.")}>
              Batalkan
            </button>
          ) : null}
          {job && !active ? (
            <>
              <button className="btn" type="button" disabled={actionBusy} onClick={() => setConfirming("redispatch")}>
                Proses ulang
              </button>
              <button className="btn" type="button" disabled={actionBusy} onClick={() => setConfirming("rescore")}>
                Analisis ulang
              </button>
            </>
          ) : null}
          {/* Aksi destruktif dipisah jarak dan warna agar tidak tertekan tak sengaja. */}
          <button className="btn btn-danger" type="button" disabled={actionBusy || !job} onClick={() => setConfirming("delete")} style={{ marginLeft: "0.5rem" }}>
            Hapus
          </button>
        </div>
      </div>

      {confirming ? (
        <ConfirmDialog
          open
          {...ACTION_CONFIRM[confirming]}
          onConfirm={() => performAction(confirming)}
          onCancel={() => setConfirming(null)}
        />
      ) : null}

      {job?.error && !active ? (
        <p className="alert" role="status" style={{ margin: 0 }}>
          {job.error}
        </p>
      ) : null}
      {error ? (
        <p className="alert" role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      ) : null}

      <div role="tablist" aria-label="Mode kerja" className="tabs" onKeyDown={onTabKey}>
        {TABS.map((t) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className="tab"
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === "segments" && segments ? ` (${segments.length})` : ""}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "log" ? (
          <JobLogPanel jobId={jobId} status={job?.status} />
        ) : tab === "style" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <CropModePanel jobId={jobId} value={cropMode ?? "face_track"} onChange={setCropMode} />
            <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
              Mode dan gaya berlaku untuk render berikutnya — tekan Render ulang di tab Klip.
            </p>
            <CaptionStylePanel jobId={jobId} />
          </div>
        ) : tab === "editor" ? (
          <TranscriptEditor jobId={jobId} />
        ) : (
          (() => {
            const currentSegmentId =
              selectedSegmentId || (segments && segments.length > 0 ? segments[0].id : null);
            // Daftar render terurut created_at menurun: find() = render terbaru.
            const latest = (segmentId: string | null, kind: "final" | "preview") =>
              renders.find((r) => r.segment_id === segmentId && r.kind === kind);
            const doneOf = (segmentId: string | null, kind: "final" | "preview") =>
              renders.find((r) => r.segment_id === segmentId && r.kind === kind && r.status === "done");
            const activeFinal = doneOf(currentSegmentId, "final");
            const currentVideo = activeFinal || doneOf(currentSegmentId, "preview");
            const finalsDone = (segments ?? [])
              .map((s) => doneOf(s.id, "final"))
              .filter((r): r is Render => r !== undefined);
            const downloadAll = () => {
              // ponytail: satu <a download> per klip; browser bisa minta izin "unduh banyak berkas".
              for (const r of finalsDone) {
                const a = document.createElement("a");
                a.href = api.jobRenderDownloadUrl(jobId, r.id);
                a.download = "";
                a.click();
              }
            };
            const isPending =
              renders.some((r) => r.segment_id === currentSegmentId && (r.status === "queued" || r.status === "running")) ||
              (renderingSegmentId !== null && renderingSegmentId === currentSegmentId);
            const currentIndex = segments ? segments.findIndex((s) => s.id === currentSegmentId) : -1;
            const currentSegment = currentIndex >= 0 && segments ? segments[currentIndex] : null;

            return (
              <div className="split split-lead">
                <div>
                  <div
                    style={{
                      aspectRatio: "9 / 16",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: "0.6rem",
                      background: "var(--well)",
                      border: "var(--bw) solid var(--ink)",
                      boxShadow: "var(--shadow)",
                      color: "var(--paper)",
                      overflow: "hidden",
                      position: "relative",
                    }}
                  >
                    {currentVideo ? (
                      <video
                        key={currentVideo.id}
                        controls
                        playsInline
                        src={api.jobRenderFileUrl(jobId, currentVideo.id)}
                        style={{ width: "100%", height: "100%", objectFit: "contain", background: "#000" }}
                      />
                    ) : segments === null ? (
                      <div className="label" style={{ color: "var(--paper)", opacity: 0.7 }}>Memuat…</div>
                    ) : isPending ? (
                      <>
                        <span className="tally" style={{ width: 14, height: 14 }} />
                        <div className="label" style={{ color: "var(--accent)" }}>Merender klip</div>
                        <div className="mono" style={{ fontSize: "0.75rem", opacity: 0.85, textAlign: "center", padding: "0 1.5rem" }}>
                          Memotong 9:16 dan membakar takarir. Klip muncul di sini begitu selesai.
                        </div>
                      </>
                    ) : (
                      <div className="mono" style={{ fontSize: "0.75rem", opacity: 0.85, textAlign: "center", padding: "0 1.5rem" }}>
                        {segments && segments.length > 0
                          ? "Klip ini belum dirender. Tekan Render untuk membuatnya."
                          : "Klip muncul di sini setelah analisis selesai."}
                      </div>
                    )}
                  </div>

                  {currentSegment ? (
                    <div style={{ marginTop: "0.85rem", display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
                        <span className="label">Klip {currentIndex + 1}</span>
                        {currentVideo ? (
                          <span className="muted" style={{ fontSize: "0.8rem" }}>
                            {currentVideo.kind === "final" ? "1080 × 1920" : "Pratinjau 540p"}
                          </span>
                        ) : null}
                      </div>
                      {activeFinal ? (
                        <a href={api.jobRenderDownloadUrl(jobId, activeFinal.id)} className="btn btn-primary" download>
                          Unduh klip {currentIndex + 1}
                        </a>
                      ) : null}
                      <SocialCaptionBox jobId={jobId} segmentId={currentSegment.id} />
                    </div>
                  ) : null}
                </div>

                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "0.75rem",
                      flexWrap: "wrap",
                      marginBottom: "1rem",
                    }}
                  >
                    <div className="muted" style={{ fontSize: "0.9rem" }}>
                      {segments && segments.length > 0 ? `${finalsDone.length} dari ${segments.length} klip siap diunduh` : null}
                    </div>
                    {finalsDone.length > 0 ? (
                      <button type="button" className="btn btn-primary" onClick={downloadAll}>
                        Unduh semua ({finalsDone.length})
                      </button>
                    ) : null}
                  </div>

                  {segments === null ? (
                    <div className="skeleton" style={{ height: 140 }} />
                  ) : segments.length === 0 ? (
                    <div className="panel">
                      <h3 style={{ margin: 0 }}>Belum ada klip</h3>
                      <p className="muted" style={{ fontSize: "0.875rem", margin: "0.4rem 0 0" }}>
                        {note || "Klip muncul setelah transkripsi dan analisis selesai."}
                      </p>
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {segments.map((segment, index) => {
                        const isSelected = segment.id === currentSegmentId;
                        const segFinal = latest(segment.id, "final");
                        const segPreview = latest(segment.id, "preview");
                        const isRendering =
                          renderingSegmentId === segment.id ||
                          [segFinal?.status, segPreview?.status].some((st) => st === "running" || st === "queued");
                        const downloadable = doneOf(segment.id, "final");
                        const select = () => setSelectedSegmentId(segment.id);

                        return (
                          <article
                            key={segment.id}
                            className="panel"
                            role="button"
                            tabIndex={0}
                            aria-pressed={isSelected}
                            aria-label={`Putar klip ${index + 1}: ${segment.label ?? "tanpa judul"}`}
                            style={{
                              cursor: "pointer",
                              background: isSelected ? "var(--highlight)" : undefined,
                              boxShadow: isSelected ? "var(--shadow-lg)" : undefined,
                              // Penanda terpilih selain warna: tepi atas tebal.
                              borderTopWidth: isSelected ? 8 : undefined,
                            }}
                            onClick={select}
                            onKeyDown={(e) => {
                              if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                                e.preventDefault();
                                select();
                              }
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
                              <div style={{ minWidth: 0, flex: "1 1 260px" }}>
                                <div className="mono muted" style={{ fontSize: "0.8rem", display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
                                  <span style={{ color: "var(--ink)" }}>Klip {index + 1}</span>
                                  <span>
                                    {formatTimecode(segment.start_s)} – {formatTimecode(segment.end_s)}
                                  </span>
                                  <span>{Math.round(segment.end_s - segment.start_s)} detik</span>
                                </div>
                                <h3 style={{ margin: "0.35rem 0 0" }}>{segment.label}</h3>
                              </div>

                              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                                {isRendering ? (
                                  <span role="status" style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", fontSize: "0.85rem" }}>
                                    <span className="tally" /> Merender
                                  </span>
                                ) : segFinal?.status === "failed" ? (
                                  <span className="badge badge-failed">Render gagal</span>
                                ) : null}
                                {downloadable && !isRendering ? (
                                  <a
                                    href={api.jobRenderDownloadUrl(jobId, downloadable.id)}
                                    className="btn btn-primary"
                                    download
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    Unduh
                                  </a>
                                ) : null}
                                {!isRendering ? (
                                  <button
                                    type="button"
                                    className="btn"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      void handleRender(segment.id);
                                    }}
                                  >
                                    {segFinal ? "Render ulang" : "Render"}
                                  </button>
                                ) : null}
                              </div>
                            </div>

                            {segment.reason ? (
                              <p className="muted" style={{ fontSize: "0.875rem", margin: "0.6rem 0 0" }}>
                                {segment.reason}
                              </p>
                            ) : null}

                            <div
                              style={{
                                display: "grid",
                                gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
                                gap: "0.75rem 1.25rem",
                                marginTop: "0.85rem",
                              }}
                            >
                              <ScoreMeter label="Skor" value={segment.score === null ? null : segment.score > 1 ? segment.score / 100 : segment.score} />
                              <ScoreMeter label="Hook" value={segment.hook_score} />
                              <ScoreMeter label="Kelengkapan" value={segment.completeness} />
                              <ScoreMeter label="Emosi" value={segment.emotional_arc} />
                            </div>

                            <details style={{ marginTop: "0.85rem" }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                              <summary className="muted" style={{ cursor: "pointer", fontSize: "0.85rem" }}>
                                Ubah rentang
                              </summary>
                              <SegmentTrim jobId={jobId} segment={segment} onSaved={() => void load()} />
                            </details>

                            <details
                              style={{ marginTop: "0.5rem" }}
                              onClick={(e) => e.stopPropagation()}
                              onKeyDown={(e) => e.stopPropagation()}
                              onToggle={(e) => {
                                const open = (e.currentTarget as HTMLDetailsElement).open;
                                setOpenOverlays((current) => {
                                  const next = new Set(current);
                                  if (open) next.add(segment.id);
                                  else next.delete(segment.id);
                                  return next;
                                });
                              }}
                            >
                              <summary className="muted" style={{ cursor: "pointer", fontSize: "0.85rem" }}>
                                B-roll dan efek suara
                              </summary>
                              {/* Dimuat hanya saat dibuka: 30 segmen x 3 permintaan per halaman terlalu boros. */}
                              {openOverlays.has(segment.id) ? (
                                <OverlayTimeline segmentId={segment.id} durationS={segment.end_s - segment.start_s} />
                              ) : null}
                            </details>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
}