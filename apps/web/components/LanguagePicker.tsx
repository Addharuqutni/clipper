"use client";

// Pemilih bahasa ucapan video — dipakai halaman YouTube dan Upload.
//
// Bawaan Indonesia: deteksi otomatis Whisper menebak dari 30 detik pertama,
// dan intro musik atau pembuka berbahasa Inggris membuat seluruh transkrip
// video berbahasa Indonesia keluar dalam bahasa yang salah. Nilai ini juga
// menentukan trek subtitle YouTube yang boleh dipakai sebagai transkrip.
import type { JobLanguage } from "@/lib/types";

const OPTIONS: { value: JobLanguage; label: string }[] = [
  { value: "id", label: "Indonesia" },
  { value: "en", label: "English" },
  { value: "auto", label: "Otomatis" },
];

const HINT: Record<JobLanguage, string> = {
  id: "Transkrip dan subtitle dibuat dalam bahasa Indonesia.",
  en: "Transkrip dan subtitle dibuat dalam bahasa Inggris.",
  auto: "Bahasa ditebak dari awal video. Bisa salah bila video dibuka musik atau bahasa lain.",
};

interface Props {
  id: string;
  value: JobLanguage;
  onChange: (value: JobLanguage) => void;
  disabled?: boolean;
}

export default function LanguagePicker({ id, value, onChange, disabled }: Props) {
  return (
    <div style={{ marginBottom: "0.85rem" }}>
      <div id={`${id}-label`} className="label">
        bahasa video
      </div>
      <div
        className="clip-presets"
        role="group"
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-hint`}
        style={{ marginTop: "0.5rem" }}
      >
        {OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === value ? "btn btn-primary" : "btn"}
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            disabled={disabled}
          >
            {option.label}
          </button>
        ))}
      </div>
      <p id={`${id}-hint`} className="muted clip-picker-hint">
        {HINT[value]}
      </p>
    </div>
  );
}
