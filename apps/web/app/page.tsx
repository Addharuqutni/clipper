// Landing — gaya Neo-Brutalism. Alur user journey PRD §3 sebagai stepper.
import Link from "next/link";

const STEPS = [
  { t: "Input", d: "URL YouTube atau berkas lokal (.mp4/.mov/.mkv, maks 3 GB)." },
  { t: "Pra-proses", d: "Multipart upload / fetch YouTube + validasi." },
  { t: "Transkripsi", d: "ASR faster-whisper dengan timestamp." },
  { t: "Analisis", d: "Skor momen viral, pilih segmen klip." },
  { t: "Render", d: "Crop 9:16 + takarir kinetik + reframe." },
  { t: "Unduh", d: "Unduh MP4 final 1080×1920, lalu unggah manual ke platform pilihan." },
];

// Batas nyata aplikasi — ditampilkan sebagai strip spesifikasi di hero,
// bukan deretan kartu angka yang terpisah dari konteksnya.
const SPECS = [
  { k: "Masukan maks", v: "3 GB" },
  { k: "Keluaran", v: "9:16" },
  { k: "Durasi sumber", v: "Ikut model AI" },
  { k: "Klip per video", v: "1–30" },
];

export default function Home() {
  return (
    <div className="page">
      {/* HERO — blok kanonik brutalism: kuning, border tebal, shadow keras. */}
      <section
        className="panel"
        style={{
          background: "var(--accent)",
          boxShadow: "var(--shadow-lg)",
          padding: 0,
        }}
      >
        <div style={{ padding: "clamp(1.5rem, 4vw, 2.75rem) clamp(1.25rem, 4vw, 2.5rem)" }}>
          <h1 style={{ maxWidth: "14ch", margin: 0 }}>Potong video panjang jadi klip 9:16</h1>
          <p style={{ fontSize: "1.1rem", fontWeight: 600, maxWidth: "56ch", margin: "1.1rem 0 0.5rem" }}>
            ClipperAI mengubah video panjang menjadi klip vertikal 9:16 siap unduh.
            Unggah berkas atau tempel URL YouTube, lalu biarkan AI menemukan momen
            terbaiknya.
          </p>
          <p className="mono" style={{ fontSize: "0.8rem", margin: "0 0 1.5rem" }}>
            Tujuan: dari 60–90 menit editing manual menjadi hitungan menit per klip.
          </p>

          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <Link href="/upload" className="btn btn-lg btn-dark">
              Mulai Upload
            </Link>
            <Link href="/youtube" className="btn btn-lg">
              Dari YouTube
            </Link>
          </div>
        </div>

        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            margin: 0,
            borderTop: "var(--bw) solid var(--ink)",
            background: "var(--panel)",
          }}
        >
          {SPECS.map((s, i) => (
            <div
              key={s.k}
              style={{
                padding: "0.9rem clamp(1.25rem, 4vw, 2.5rem) 1rem",
                borderLeft: i === 0 ? "none" : "var(--bw-thin) solid var(--ink)",
                marginLeft: i === 0 ? 0 : "-2px",
              }}
            >
              <dt className="label muted">{s.k}</dt>
              <dd style={{ margin: "0.3rem 0 0", fontFamily: "var(--font-display)", fontSize: "1.6rem", lineHeight: 1 }}>
                {s.v}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ALUR — urutan tahap memang informasi (pipeline berurutan), jadi nomor dipertahankan. */}
      <section aria-labelledby="alur" style={{ marginTop: "1rem" }}>
        <h2 id="alur" style={{ margin: "0 0 1rem" }}>Alur kerja</h2>
        <ol
          className="grid"
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", listStyle: "none", padding: 0, margin: 0 }}
        >
          {STEPS.map((s, i) => (
            <li key={s.t} className="panel" style={{ display: "flex", gap: "1rem", alignItems: "flex-start" }}>
              <span
                aria-hidden="true"
                style={{
                  flex: "none",
                  display: "grid",
                  placeItems: "center",
                  width: "2.6rem",
                  height: "2.6rem",
                  background: "var(--ink)",
                  color: "var(--accent)",
                  fontFamily: "var(--font-display)",
                  fontSize: "1.1rem",
                }}
              >
                {i + 1}
              </span>
              <div style={{ minWidth: 0 }}>
                <h3 style={{ margin: "0.1rem 0 0.35rem" }}>{s.t}</h3>
                <p className="muted" style={{ fontSize: "0.92rem", margin: 0 }}>
                  {s.d}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* CTA PENUTUP */}
      <section
        className="panel"
        style={{
          background: "var(--toxic)",
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "1.25rem",
          flexWrap: "wrap",
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: "1.5rem" }}>Siap memangkas waktu editing?</h2>
          <p style={{ fontSize: "0.92rem", fontWeight: 600, margin: "0.4rem 0 0" }}>
            Batas durasi mengikuti kapasitas konteks model AI di Setelan.
          </p>
        </div>
        <Link href="/dashboard" className="btn btn-lg btn-dark">
          Buka Dashboard
        </Link>
      </section>
    </div>
  );
}
