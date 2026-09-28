// Halaman Upload — gaya Neo-Brutalism.
import Uploader from "@/components/Uploader";

const RULES = [
  { k: "Format", v: ".mp4 / .mov / .mkv" },
  { k: "Ukuran maks", v: "3 GB" },
  { k: "Metode", v: "Per bagian 10 MB" },
  { k: "Resume", v: "Pilih berkas yang sama lagi" },
];

export default function UploadPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Unggah video</h1>
        </div>
      </div>

      <p className="muted" style={{ maxWidth: "62ch", margin: 0 }}>
        Berkas dikirim per bagian ke aplikasi di komputer ini. Bila dijeda,
        tab ditutup, atau aplikasi dimulai ulang, pilih berkas yang sama lalu
        tekan Upload: unggahan dilanjutkan dari bagian terakhir, bukan dari nol.
      </p>


      <div className="split">
        <Uploader />

        <aside className="panel">
          <div className="label">Ketentuan berkas</div>
          <dl style={{ margin: "0.75rem 0 0" }}>
            {RULES.map((r) => (
              <div
                key={r.k}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "0.75rem",
                  borderTop: "var(--bw-thin) solid var(--line)",
                  padding: "0.5rem 0",
                }}
              >
                <dt className="muted" style={{ fontSize: "0.75rem", margin: 0 }}>
                  {r.k}
                </dt>
                <dd className="mono" style={{ fontSize: "0.75rem", margin: 0, fontWeight: 900 }}>
                  {r.v}
                </dd>
              </div>
            ))}
          </dl>

          <p className="mono" style={{ fontSize: "0.68rem", marginTop: "1rem", marginBottom: 0 }}>
            Berkas mentah dihapus otomatis 48 jam setelah render terakhir selesai.
          </p>
        </aside>
      </div>
    </div>
  );
}
