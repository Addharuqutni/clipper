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

export default function Home() {
  return (
    <div className="page">
      {/* HERO — blok kanonik brutalism: krem, border tebal, shadow keras. */}
      <section
        className="panel"
        style={{
          background: "var(--accent)",
          boxShadow: "var(--shadow-lg)",
          padding: "2rem 1.5rem",
        }}
      >
        <div className="label" style={{ marginBottom: "0.6rem" }}>
          AI Video Repurposing
        </div>
        <h1 style={{ maxWidth: "16ch", margin: 0 }}>
          Potong video panjang jadi klip 9:16
        </h1>
        <p
          style={{
            fontSize: "1.05rem",
            maxWidth: "58ch",
            marginTop: "1rem",
            marginBottom: "0.5rem",
          }}
        >
          ClipperAI mengubah video panjang menjadi klip vertikal 9:16 siap unduh.
          Unggah berkas atau tempel URL YouTube, lalu biarkan AI menemukan momen
          terbaiknya.
        </p>
        <p className="mono" style={{ fontSize: "0.78rem", margin: "0 0 0.5rem" }}>
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
      </section>

      {/* STATISTIK — blok angka besar, tanda tangan dashboard brutalism. */}
      <div className="grid grid-4">
        <div className="panel">
          <div className="label muted">Masukan maks</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, lineHeight: 1 }}>3 GB</div>
        </div>
        <div className="panel">
          <div className="label muted">Keluaran</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, lineHeight: 1 }}>9:16</div>
        </div>
        <div className="panel">
          <div className="label muted">Durasi sumber</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, lineHeight: 1 }}>≤ 180′</div>
        </div>
        <div className="panel">
          <div className="label muted">Segmen / video</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, lineHeight: 1 }}>1–30</div>
        </div>
      </div>

      {/* ALUR — stepper bernomor blok. */}
      <h2 style={{ marginBottom: 0 }}>Alur Kerja</h2>
      <ol className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", listStyle: "none", padding: 0, margin: 0 }}>
        {STEPS.map((s, i) => (
          <li key={s.t} className="panel panel-interactive" style={{ padding: "0" }}>
            <div
              style={{
                background: "var(--ink)",
                color: "var(--accent)",
                fontWeight: 900,
                fontSize: "1.5rem",
                padding: "0.4rem 0.85rem",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{String(i + 1).padStart(2, "0")}</span>
              <span className="label" style={{ color: "var(--paper)", opacity: 0.7 }}>
                Tahap
              </span>
            </div>
            <div style={{ padding: "0.9rem 1rem" }}>
              <h3 style={{ marginBottom: "0.35rem" }}>{s.t}</h3>
              <div className="muted" style={{ fontSize: "0.85rem", fontWeight: 700 }}>
                {s.d}
              </div>
            </div>
          </li>
        ))}
      </ol>

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
          <p className="mono" style={{ fontSize: "0.78rem", margin: "0.4rem 0 0" }}>
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
