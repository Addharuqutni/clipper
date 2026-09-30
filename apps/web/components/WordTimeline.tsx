"use client";

// Timeline kata untuk editor transkrip.
//
// **Mengapa jendela waktu, bukan satu batang untuk seluruh video.** Transkrip
// 40 menit berisi ~8000 kata; dipadatkan ke satu batang, tiap kata menjadi
// garis 2px tanpa teks — tidak bisa dibaca, sulit diklik. Timeline ini dibagi
// dua: batang ringkasan (seluruh durasi, klik untuk melompat) dan jendela
// perbesaran (5–30 detik) yang menampilkan teks kata pada posisi waktunya.
// Hanya kata di dalam jendela yang dirender, jadi ribuan kata tetap ringan.
//
// Jendela mengikuti pemutar (berpindah halaman saat playhead keluar), kecuali
// pengguna sendiri yang menggeser jendela — lalu tombol "Ikuti pemutar"
// mengembalikannya.
import { useMemo, useState } from "react";
import type { TranscriptWord } from "@/lib/types";

interface Props {
  words: TranscriptWord[];
  duration: number;
  currentTime: number;
  activeIndex: number | null;
  /** Lompat ke detik tertentu; `index` diisi bila yang diklik adalah kata. */
  onSeek: (seconds: number, index?: number) => void;
  /** Mulai mengedit kata (klik ganda). */
  onEdit: (index: number) => void;
}

const ZOOMS = [5, 10, 20] as const;

/** Indeks kata pertama yang berakhir setelah `t` (kata terurut per waktu). */
function firstEndingAfter(words: TranscriptWord[], t: number): number {
  let lo = 0;
  let hi = words.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].end_s <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export default function WordTimeline({ words, duration, currentTime, activeIndex, onSeek, onEdit }: Props) {
  const [zoom, setZoom] = useState<number>(5);
  const [follow, setFollow] = useState(true);
  const [manualStart, setManualStart] = useState(0);

  const maxStart = Math.max(0, duration - zoom);
  const followStart = Math.min(maxStart, Math.floor(currentTime / zoom) * zoom);
  const start = follow ? followStart : Math.min(maxStart, Math.max(0, manualStart));
  const end = start + zoom;

  const visible = useMemo(() => {
    const out: { word: TranscriptWord; index: number }[] = [];
    for (let i = firstEndingAfter(words, start); i < words.length && words[i].start_s < end; i++) {
      out.push({ word: words[i], index: i });
    }
    return out;
  }, [words, start, end]);

  const tickStep = zoom <= 10 ? 1 : 5;
  const ticks = useMemo(() => {
    const out: number[] = [];
    for (let t = Math.ceil(start / tickStep) * tickStep; t <= end; t += tickStep) out.push(t);
    return out;
  }, [start, end, tickStep]);

  const pctIn = (t: number) => ((t - start) / zoom) * 100;
  const pctAll = (t: number) => (duration > 0 ? Math.min(100, (t / duration) * 100) : 0);

  const page = (direction: -1 | 1) => {
    setFollow(false);
    setManualStart(Math.min(maxStart, Math.max(0, start + direction * zoom)));
  };

  const playheadInWindow = currentTime >= start && currentTime <= end;

  return (
    <div style={{ marginTop: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <span className="label">Timeline</span>
        <span className="mono" style={{ fontSize: "0.72rem", color: "var(--muted-ink)" }}>
          {clock(start)} – {clock(end)}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
          <button className="btn" type="button" onClick={() => page(-1)} disabled={start <= 0} aria-label="Jendela sebelumnya" style={smallBtn}>
            ◀
          </button>
          <button className="btn" type="button" onClick={() => page(1)} disabled={start >= maxStart} aria-label="Jendela berikutnya" style={smallBtn}>
            ▶
          </button>
          <button
            className={follow ? "btn btn-primary" : "btn"}
            type="button"
            onClick={() => setFollow(true)}
            aria-pressed={follow}
            title="Jendela timeline berpindah mengikuti posisi video"
            style={smallBtn}
          >
            Ikuti pemutar
          </button>
          <div role="group" aria-label="Perbesaran timeline" style={{ display: "flex" }}>
            {ZOOMS.map((z) => (
              <button
                key={z}
                className={z === zoom ? "btn btn-primary" : "btn"}
                type="button"
                aria-pressed={z === zoom}
                onClick={() => {
                  setManualStart(start);
                  setZoom(z);
                }}
                style={smallBtn}
              >
                {z}s
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Ringkasan seluruh durasi: klik untuk melompat; kotak = jendela aktif. */}
      <div
        role="slider"
        tabIndex={0}
        aria-label="Posisi di seluruh video"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(currentTime)}
        aria-valuetext={clock(currentTime)}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setFollow(true);
          onSeek(((e.clientX - rect.left) / rect.width) * duration);
        }}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 30 : 5;
          if (e.key === "ArrowRight") onSeek(Math.min(duration, currentTime + step));
          else if (e.key === "ArrowLeft") onSeek(Math.max(0, currentTime - step));
          else return;
          e.preventDefault();
          setFollow(true);
        }}
        style={{
          position: "relative",
          height: 14,
          marginTop: "0.5rem",
          background: "var(--muted-bg)",
          border: "var(--bw-thin) solid var(--line)",
          cursor: "pointer",
        }}
      >
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${pctAll(start)}%`,
            width: `max(4px, ${pctAll(zoom)}%)`,
            background: "var(--accent)",
            borderLeft: "1px solid var(--ink)",
            borderRight: "1px solid var(--ink)",
          }}
        />
        <div
          aria-hidden="true"
          style={{ position: "absolute", top: -3, bottom: -3, left: `${pctAll(currentTime)}%`, width: 2, background: "var(--rec)" }}
        />
      </div>

      {/* Jendela perbesaran: kata sebagai blok berlabel pada posisi waktunya. */}
      <div
        style={{
          position: "relative",
          marginTop: "0.5rem",
          border: "var(--bw) solid var(--line)",
          background: "var(--panel)",
          height: 112,
          overflow: "hidden",
        }}
      >
        {/* Penggaris waktu; klik area kosong untuk melompat ke detik itu. */}
        <div
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            onSeek(start + ((e.clientX - rect.left) / rect.width) * zoom);
          }}
          style={{ position: "absolute", inset: 0, cursor: "crosshair" }}
          aria-hidden="true"
        >
          {ticks.map((t) => (
            <div
              key={t}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: `${pctIn(t)}%`,
                borderLeft: "1px dashed var(--muted-bg)",
              }}
            >
              <span className="mono" style={{ position: "absolute", top: 2, left: 3, fontSize: "0.65rem", color: "var(--muted-ink)", whiteSpace: "nowrap" }}>
                {clock(t)}
              </span>
            </div>
          ))}
        </div>

        <div role="list" aria-label="Kata dalam jendela timeline">
          {visible.map(({ word, index }) => {
            const isActive = index === activeIndex;
            const left = Math.max(0, pctIn(word.start_s));
            // Durasi ucapan sebenarnya, digambar sebagai garis di dasar chip.
            const spoken = Math.max(0.6, pctIn(Math.min(end, word.end_s)) - left);
            // Dua lajur bergantian; chip boleh memanjang sampai kata berikutnya
            // di lajur yang sama (index + 2) supaya teksnya terbaca utuh.
            // Posisi kiri tetap = detik mulai, jadi urutan waktu tidak berubah.
            const nextSameLane = words[index + 2]?.start_s ?? end;
            const room = pctIn(Math.min(end, nextSameLane)) - left - 0.4;
            const width = Math.max(spoken, room);
            const lane = index % 2;
            return (
              <button
                key={`${word.index}-${index}`}
                role="listitem"
                type="button"
                title={`${word.text} · ${word.start_s.toFixed(2)}–${word.end_s.toFixed(2)}s · klik ganda untuk mengedit`}
                aria-label={`${word.text}, ${word.start_s.toFixed(1)} detik. Enter untuk melompat, F2 untuk mengedit.`}
                aria-current={isActive ? "true" : undefined}
                onClick={() => onSeek(word.start_s, index)}
                onDoubleClick={() => onEdit(index)}
                onKeyDown={(e) => {
                  if (e.key === "F2") {
                    e.preventDefault();
                    onEdit(index);
                  }
                }}
                style={{
                  position: "absolute",
                  top: lane === 0 ? 24 : 66,
                  height: 36,
                  left: `${left}%`,
                  width: `${width}%`,
                  minWidth: 10,
                  padding: "0 4px",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                  textAlign: "left",
                  fontSize: "0.78rem",
                  fontWeight: isActive ? 900 : 600,
                  color: "var(--ink)",
                  background: isActive ? "var(--accent)" : "var(--highlight)",
                  border: `var(--bw-thin) solid var(--line)`,
                  boxShadow: isActive ? "var(--shadow-sm)" : "none",
                  cursor: "pointer",
                  zIndex: isActive ? 2 : 1,
                }}
              >
                {word.text}
                <span
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    left: 0,
                    bottom: 0,
                    height: 4,
                    width: `${(spoken / width) * 100}%`,
                    background: "var(--ink)",
                    opacity: 0.35,
                  }}
                />
              </button>
            );
          })}
        </div>

        {playheadInWindow ? (
          <div
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: `${pctIn(currentTime)}%`,
              width: 2,
              background: "var(--rec)",
              zIndex: 3,
              pointerEvents: "none",
            }}
          />
        ) : null}
      </div>

      <p className="muted" style={{ fontSize: "0.72rem", marginTop: "0.4rem", marginBottom: 0 }}>
        Klik kata untuk melompat, klik ganda (atau F2) untuk mengedit. Klik batang atas untuk
        pindah ke bagian lain video; ◀ ▶ menggeser jendela.
      </p>
    </div>
  );
}

const smallBtn = { fontSize: "0.72rem", padding: "0.2rem 0.5rem", minHeight: 32 } as const;
