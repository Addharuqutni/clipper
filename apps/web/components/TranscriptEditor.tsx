"use client";

// Editor transkrip interaktif: pemutar video sumber + timeline kata + editor teks.
//
// **Mengapa pemutar video, bukan daftar kata saja.** Memperbaiki salah dengar
// tanpa mendengar konteksnya adalah menebak. Pengguna perlu melompat ke detik
// kata itu diucapkan, mendengarnya, lalu memperbaiki teksnya. Timeline yang
// bisa diklik adalah yang menghubungkan keduanya: klik kata → video melompat.
//
// **Penyimpanan yang tahan banting.** Setiap perubahan disimpan langsung ke
// server (PATCH). Menyimpan borongan saat halaman ditutup berarti kehilangan
// seluruh perbaikan bila tab ditutup — dan pengguna hampir selalu menutup tab
// tepat setelah selesai. Status "menyimpan/menyesuaikan/tersimpan" ditampilkan
// supaya pengguna tahu perubahan benar-benar tersimpan, bukan sekadar terlihat
// berubah di layar.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api/client";
import type { TranscriptWord } from "@/lib/types";

interface Props {
  jobId: string;
}

/** Keadaan penyimpanan satu kata. */
type SaveState = "idle" | "saving" | "saved" | "error";

/**
 * Kunci stabil satu kata untuk status simpan. Indeks array bergeser setelah
 * kata dihapus (penanda "tersimpan" pindah ke kata lain); waktu mulai tidak.
 */
const wordKey = (w: TranscriptWord) => w.start_s.toFixed(3);

export default function TranscriptEditor({ jobId }: Props) {
  const [words, setWords] = useState<TranscriptWord[]>([]);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  /** Gagal simpan satu kata: ditampilkan di atas daftar, editor tetap bisa dipakai. */
  const [saveError, setSaveError] = useState<string | null>(null);
  const [language, setLanguage] = useState<string | null>(null);

  // Kata yang sedang aktif (diputar atau diklik).
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  // Kata yang sedang diedit (null = tidak ada).
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saveState, setSaveState] = useState<Record<string, SaveState>>({});
  const [filter, setFilter] = useState("");

  const videoRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const activeRowRef = useRef<HTMLDivElement>(null);
  // Menahan auto-scroll saat pengguna sendiri yang menggulir daftar — kalau
  // tidak, daftar akan melompat balik ke kata aktif dan mengganggu pengeditan.
  const [autoScroll, setAutoScroll] = useState(true);

  const mediaUrl = useMemo(() => api.jobMediaFileUrl(jobId), [jobId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getTranscript(jobId)
      .then((data) => {
        if (cancelled) return;
        setWords(data.words);
        setNote(data.note);
        setLanguage(data.language);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : "Gagal memuat transkrip.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  /** Lompat pemutar ke detik tertentu. */
  const seekTo = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    void video.play().catch(() => {
      // Autoplay bisa ditolak browser; melompat tetap berhasil tanpa play.
    });
  }, []);

  /** Tandai kata aktif berdasarkan waktu pemutar (dipanggil dari onTimeUpdate). */
  const syncActiveFromVideo = useCallback(() => {
    const video = videoRef.current;
    if (!video || editingIndex !== null) return;
    const t = video.currentTime;
    // Cari kata yang waktunya melingkupi t. Daftar kecil (ratusan kata), jadi
    // pencarian linear murni lebih murah daripada membangun indeks.
    const found = words.findIndex((w) => t >= w.start_s && t < w.end_s);
    if (found >= 0) setActiveIndex(found);
  }, [words, editingIndex]);

  // Gulir DAFTAR (bukan halaman) agar kata aktif tetap terlihat.
  // scrollIntoView menggulir semua leluhur, termasuk halaman — mengganggu saat
  // pengguna sedang membaca bagian lain.
  useEffect(() => {
    if (!autoScroll || activeIndex === null) return;
    const list = listRef.current;
    const row = activeRowRef.current;
    if (!list || !row) return;
    const top = row.offsetTop;
    if (top < list.scrollTop || top + row.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTo({ top: top - list.clientHeight / 2, behavior: "smooth" });
    }
  }, [activeIndex, autoScroll]);

  const startEdit = (index: number, current: string) => {
    setEditingIndex(index);
    setDraft(current);
    // Jeda video saat mengedit: memutar sambil mengetik membuat teks bergerak
    // dan kata yang diedit bisa berpindah posisi di layar.
    videoRef.current?.pause();
  };

  const cancelEdit = () => {
    setEditingIndex(null);
    setDraft("");
  };

  /** Simpan perubahan satu kata; teks kosong berarti menghapus kata. */
  const commitEdit = async (index: number) => {
    const word = words[index];
    if (!word) return;
    const text = draft.trim();
    if (text === word.text) {
      cancelEdit();
      return;
    }

    const key = wordKey(word);
    setSaveState((s) => ({ ...s, [key]: "saving" }));
    setSaveError(null);
    try {
      const result = await api.updateWord(jobId, word.index, text, word.text);
      // Muat ulang dari server alih-alih menambal state lokal: setelah
      // penghapusan, SEMUA indeks bergeser.
      const fresh = await api.getTranscript(jobId);
      setWords(fresh.words);
      setSaveState((s) => ({ ...s, [key]: "saved" }));
      setEditingIndex(null);
      setDraft("");
      if (result.removed) setActiveIndex(null);
      window.setTimeout(() => setSaveState((s) => ({ ...s, [key]: "idle" })), 1500);
    } catch (e: unknown) {
      setSaveState((s) => ({ ...s, [key]: "error" }));
      setSaveError(e instanceof ApiError ? e.message : "Gagal menyimpan perubahan.");
      // 409 = transkrip berubah di tempat lain: tampilkan versi server.
      if (e instanceof ApiError && e.status === 409) {
        const fresh = await api.getTranscript(jobId).catch(() => null);
        if (fresh) setWords(fresh.words);
        cancelEdit();
      }
    }
  };

  const visibleWords = useMemo(() => {
    if (!filter.trim()) return words.map((w, i) => ({ word: w, index: i }));
    const needle = filter.trim().toLowerCase();
    return words
      .map((w, i) => ({ word: w, index: i }))
      .filter(({ word }) => word.text.toLowerCase().includes(needle));
  }, [words, filter]);

  const duration = words.length ? words[words.length - 1].end_s : 0;

  if (loading) {
    return (
      <div className="panel" role="status">
        <span className="label muted">Memuat transkrip…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel alert" role="alert">
        {error}
      </div>
    );
  }

  if (!words.length) {
    return (
      <div className="panel" role="status">
        <span className="label">Transkrip belum tersedia</span>
        <p className="muted" style={{ fontSize: "0.82rem", margin: "0.5rem 0 0" }}>
          {note || "Belum ada kata yang bisa diedit."}
        </p>
      </div>
    );
  }

  return (
    <div className="split" style={{ alignItems: "start" }}>
      {/* --- Kolom kiri: pemutar + timeline --- */}
      <section className="panel">
        <div className="label">Pratinjau sumber</div>
        <video
          ref={videoRef}
          src={mediaUrl}
          controls
          preload="metadata"
          onTimeUpdate={syncActiveFromVideo}
          style={{
            width: "100%",
            marginTop: "0.6rem",
            background: "#000",
            border: "var(--bw) solid var(--line)",
          }}
        >
          {/* Subtitle tidak dilacak di sini; teks ditampilkan di panel kanan. */}
        </video>

        <div className="mono" style={{ fontSize: "0.7rem", marginTop: "0.5rem" }}>
          {language ? `bahasa: ${language} · ` : ""}
          {words.length} kata · {duration.toFixed(1)}s
        </div>

        {/* Timeline kata: satu blok kecil per kata, lebarnya proporsional durasi.
            Mengklik blok melompat ke kata itu. */}
        <div className="label" style={{ marginTop: "1rem" }}>
          Timeline
        </div>
        <div
          style={{
            display: "flex",
            gap: 1,
            marginTop: "0.4rem",
            height: 34,
            alignItems: "stretch",
            overflowX: "auto",
            border: "var(--bw) solid var(--line)",
            background: "var(--muted-bg)",
            padding: 2,
          }}
          role="list"
          aria-label="Timeline kata"
        >
          {words.map((w, i) => {
            const span = Math.max(0.05, w.end_s - w.start_s);
            const isActive = i === activeIndex;
            return (
              <button
                key={`${w.index}-${i}`}
                role="listitem"
                type="button"
                title={`${w.text} (${w.start_s.toFixed(1)}s)`}
                onClick={() => {
                  seekTo(w.start_s);
                  setActiveIndex(i);
                }}
                style={{
                  // Lebar minimum 2px agar kata sependek "a" tetap bisa diklik;
                  // tanpa itu, kata cepat menjadi garis tak terlihat.
                  width: `${Math.max(2, span * 14)}px`,
                  minWidth: 2,
                  border: "none",
                  cursor: "pointer",
                  background: isActive ? "var(--accent)" : "var(--ink)",
                  opacity: isActive ? 1 : 0.55,
                  padding: 0,
                }}
                aria-label={`Lompat ke ${w.text} pada ${w.start_s.toFixed(1)} detik`}
              />
            );
          })}
        </div>
        <p className="muted" style={{ fontSize: "0.7rem", marginTop: "0.35rem" }}>
          Klik blok untuk melompat ke kata tersebut.
        </p>
      </section>

      {/* --- Kolom kanan: daftar kata yang dapat diedit --- */}
      <section className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
          <span className="label">Perbaiki teks</span>
          <button
            className="btn"
            type="button"
            onClick={() => setAutoScroll((v) => !v)}
            title="Hentikan gulir otomatis agar daftar tidak melompat saat Anda mengedit"
            style={{ fontSize: "0.68rem", padding: "0.25rem 0.55rem" }}
          >
            {autoScroll ? "Gulir otomatis: aktif" : "Gulir otomatis: mati"}
          </button>
        </div>

        {saveError ? (
          <p className="alert" role="alert" style={{ margin: "0.6rem 0 0" }}>
            {saveError}
          </p>
        ) : null}

        <input
          className="input"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Cari kata…"
          style={{ marginTop: "0.6rem", marginBottom: "0.6rem" }}
          aria-label="Cari kata dalam transkrip"
        />

        <div
          ref={listRef}
          style={{ maxHeight: 420, overflowY: "auto", border: "var(--bw) solid var(--line)", padding: "0.4rem", position: "relative" }}
          onScroll={(e) => {
            // Gulir manual oleh pengguna mematikan auto-scroll agar daftar
            // tidak ditarik kembali ke kata aktif di tengah pengeditan.
            const el = e.currentTarget;
            const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
            if (!nearBottom) setAutoScroll(false);
          }}
        >
          {visibleWords.map(({ word, index }) => {
            const isActive = index === activeIndex;
            const isEditing = index === editingIndex;
            const state = saveState[wordKey(word)] ?? "idle";
            return (
              <div
                key={`${word.index}-${index}`}
                ref={isActive ? activeRowRef : undefined}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  padding: "0.2rem 0.35rem",
                  background: isActive ? "var(--accent)" : "transparent",
                  borderBottom: "1px solid var(--muted-bg)",
                }}
              >
                <button
                  type="button"
                  className="mono"
                  onClick={() => {
                    seekTo(word.start_s);
                    setActiveIndex(index);
                  }}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--muted-ink)",
                    fontSize: "0.65rem",
                    minWidth: "3.2rem",
                    textAlign: "left",
                    padding: 0,
                  }}
                  title="Lompat ke kata ini"
                >
                  {word.start_s.toFixed(1)}s
                </button>

                {isEditing ? (
                  <>
                    <input
                      className="input"
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void commitEdit(index);
                        if (e.key === "Escape") cancelEdit();
                      }}
                      style={{ flex: 1, fontSize: "0.82rem", padding: "0.2rem 0.4rem" }}
                      aria-label={`Ubah teks kata pada ${word.start_s.toFixed(1)} detik`}
                    />
                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={() => void commitEdit(index)}
                      disabled={state === "saving"}
                      style={{ fontSize: "0.66rem", padding: "0.2rem 0.5rem" }}
                    >
                      {state === "saving" ? "…" : "Simpan"}
                    </button>
                    <button
                      className="btn"
                      type="button"
                      onClick={cancelEdit}
                      style={{ fontSize: "0.66rem", padding: "0.2rem 0.5rem" }}
                    >
                      Batal
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => startEdit(index, word.text)}
                    style={{
                      flex: 1,
                      textAlign: "left",
                      background: "none",
                      border: "none",
                      cursor: "text",
                      fontSize: "0.82rem",
                      fontWeight: isActive ? 900 : 400,
                      padding: "0.15rem 0",
                      color: "var(--ink)",
                    }}
                    title="Klik untuk mengubah teks"
                  >
                    {word.text}
                  </button>
                )}

                {/* Penanda penyimpanan: pengguna harus bisa membedakan "terlihat
                    berubah" dari "benar-benar tersimpan". */}
                {state === "saved" ? (
                  <span className="mono" style={{ fontSize: "0.62rem", color: "var(--ink)" }}>
                    tersimpan
                  </span>
                ) : null}
                {state === "error" ? (
                  <span className="mono" style={{ fontSize: "0.62rem", color: "#b00020" }}>
                    gagal
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>

        <p className="muted" style={{ fontSize: "0.72rem", marginTop: "0.6rem", marginBottom: 0 }}>
          Klik teks untuk memperbaiki. Kosongkan lalu simpan untuk <strong>menghapus</strong> kata
          (mis. batuk yang terdeteksi sebagai kata). Enter menyimpan, Esc membatalkan.
        </p>
      </section>
    </div>
  );
}
