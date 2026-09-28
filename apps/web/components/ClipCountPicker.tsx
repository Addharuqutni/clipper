"use client";

// Pemilih jumlah klip — dipakai halaman YouTube dan Upload.
//
// Batasnya mengikuti durasi video (1 klip per 3 menit, 1–30), aturan yang sama
// dengan `clipper_shared.scoring.max_clips_for_duration` di backend. Backend
// tetap menegakkannya saat analisis; di sini hanya agar pengguna tidak memilih
// angka yang pasti dipangkas.
import { useEffect, useState } from "react";

/** Sama dengan `clipper_shared.scoring.MAX_SEGMENTS`. */
export const MAX_CLIPS = 30;
/** Sama dengan `clipper_shared.scoring.MINUTES_PER_CLIP`. */
export const MINUTES_PER_CLIP = 3;

export function maxClipsForDuration(durationS: number): number {
  return Math.max(1, Math.min(MAX_CLIPS, Math.floor(durationS / (MINUTES_PER_CLIP * 60))));
}

const PRESETS = [3, 5, 10, 20, 30];

interface Props {
  id: string;
  value: number;
  onChange: (value: number) => void;
  /** Durasi video (detik) bila sudah diketahui; `null` = belum diketahui. */
  durationS: number | null;
  disabled?: boolean;
}

export default function ClipCountPicker({ id, value, onChange, durationS, disabled }: Props) {
  const max = durationS ? maxClipsForDuration(durationS) : MAX_CLIPS;
  const clamp = (n: number) => Math.max(1, Math.min(max, Math.round(n) || 1));
  // Teks isian dipisah dari nilai: pengguna boleh mengosongkan kolom sambil
  // mengetik tanpa angka langsung melompat ke 1.
  const [text, setText] = useState(String(value));

  useEffect(() => setText(String(value)), [value]);

  // Durasi baru diketahui (berkas dipilih) dan lebih pendek: pangkas pilihan.
  useEffect(() => {
    if (value > max) onChange(max);
  }, [max, value, onChange]);

  const commit = (raw: string) => {
    // Kolom dikosongkan lalu ditinggalkan: kembali ke nilai sebelumnya.
    const next = raw.trim() === "" ? value : clamp(Number(raw));
    setText(String(next));
    if (next !== value) onChange(next);
  };

  const minutes = durationS ? Math.round(durationS / 60) : null;
  const hint =
    minutes !== null
      ? `Video ${minutes} menit → maks ${max} klip (1 per ${MINUTES_PER_CLIP} menit).`
      : `Maks ${MAX_CLIPS}. Video pendek dibatasi otomatis: 1 klip per ${MINUTES_PER_CLIP} menit.`;

  return (
    <div className="clip-picker">
      <div className="clip-picker-head">
        <label htmlFor={id} className="label">
          jumlah klip
        </label>
        <span className="mono clip-picker-total" aria-live="polite">
          ≈ {Math.round(value * 0.5)}–{value} menit hasil
        </span>
      </div>

      <div className="clip-picker-row">
        <div className="clip-stepper">
          <button
            type="button"
            className="btn"
            onClick={() => onChange(clamp(value - 1))}
            disabled={disabled || value <= 1}
            aria-label="Kurangi satu klip"
          >
            −
          </button>
          <input
            id={id}
            className="input mono"
            type="number"
            inputMode="numeric"
            min={1}
            max={max}
            value={text}
            disabled={disabled}
            onChange={(e) => setText(e.target.value)}
            onBlur={(e) => commit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit(e.currentTarget.value);
            }}
            aria-describedby={`${id}-hint`}
          />
          <button
            type="button"
            className="btn"
            onClick={() => onChange(clamp(value + 1))}
            disabled={disabled || value >= max}
            aria-label="Tambah satu klip"
          >
            +
          </button>
        </div>

        <div className="clip-presets" role="group" aria-label="Pilihan cepat jumlah klip">
          {PRESETS.filter((n) => n <= max).map((n) => (
            <button
              key={n}
              type="button"
              className={n === value ? "btn btn-primary" : "btn"}
              aria-pressed={n === value}
              onClick={() => onChange(n)}
              disabled={disabled}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      <p id={`${id}-hint`} className="muted clip-picker-hint">
        Tiap klip 30–60 detik. {hint}
      </p>
    </div>
  );
}
