"use client";
// Form URL YouTube. Validasi bentuk watch?v= / youtu.be/ (PRD FR-1.1, TECH_SPEC butir 13).
// Gaya Neo-Brutalism. Pesan error memakai role="alert" agar diumumkan
// screen reader (panduan UX: Error Messages, severity HIGH).
import { useState } from "react";
import { useRouter } from "next/navigation";
import { isValidYoutubeUrl } from "@/lib/upload/youtube";
import { api } from "@/lib/api/client";
import CookieUploader from "@/components/CookieUploader";
import AiReadinessNotice from "@/components/AiReadinessNotice";
import ClipCountPicker from "@/components/ClipCountPicker";
import LanguagePicker from "@/components/LanguagePicker";
import type { JobLanguage } from "@/lib/types";

const SYARAT = [
  "Video bersifat publik atau unlisted",
  "Durasi maksimum mengikuti konteks model AI (tertera di atas)",
  "Tautan berbentuk watch?v=, youtu.be/, atau shorts/",
];

export default function YoutubePage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [clipCount, setClipCount] = useState(5);
  const [language, setLanguage] = useState<JobLanguage>("id");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!isValidYoutubeUrl(url)) {
      setError("URL tidak valid. Gunakan youtube.com/watch?v=..., youtu.be/..., atau youtube.com/shorts/...");
      return;
    }
    setError(null);
    setLoading(true); // tombol dinonaktifkan selama proses — cegah kirim ganda
    try {
      const job = await api.submitYoutube(url.trim(), clipCount, language);
      // Halaman job menampilkan progres langsung (SSE).
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal mengirim URL.");
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Dari YouTube</h1>
        </div>
      </div>


      {/* Ditempatkan SEBELUM form: pengguna perlu tahu penyedia AI belum siap
          sebelum menekan Proses, bukan setelah menunggu proses selesai. */}
      <AiReadinessNotice />

      <div className="split">
        <section className="panel">
          <label htmlFor="yt-url" className="label">
            Tautan video
          </label>
          <input
            id="yt-url"
            className="input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !loading) void submit();
            }}
            placeholder="https://www.youtube.com/watch?v=..."
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "yt-error" : undefined}
            style={{ marginTop: "0.5rem", marginBottom: "0.85rem" }}
          />

          <LanguagePicker id="yt-lang" value={language} onChange={setLanguage} disabled={loading} />

          <ClipCountPicker
            id="yt-clips"
            value={clipCount}
            onChange={setClipCount}
            durationS={null}
            disabled={loading}
          />

          {error ? (
            <p id="yt-error" className="alert" role="alert" style={{ marginBottom: "0.85rem" }}>
              {error}
            </p>
          ) : null}

          <button
            className="btn btn-primary btn-lg"
            type="button"
            onClick={submit}
            disabled={loading}
            aria-busy={loading}
          >
            {loading ? "Mengirim…" : "Proses"}
          </button>
        </section>

        <aside className="panel">
          <div className="label">Syarat</div>
          <ul style={{ paddingLeft: "1.1rem", margin: "0.75rem 0 0" }}>
            {SYARAT.map((s) => (
              <li key={s} className="muted" style={{ fontSize: "0.9rem", marginBottom: "0.5rem" }}>
                {s}
              </li>
            ))}
          </ul>
        </aside>
      </div>

      {/* Cookies di luar .split: panelnya lebih tinggi dan berdiri sendiri,
          sehingga menaruhnya di kolom samping sempit akan memaksanya melipat. */}
      <CookieUploader />
    </div>
  );
}
