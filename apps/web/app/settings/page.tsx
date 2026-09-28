// Halaman pengaturan: penyedia AI untuk skoring viral.
//
// Ditempatkan di rute sendiri (bukan modal) supaya dapat ditautkan langsung dan
// mudah ditemukan kembali — pengguna yang salah mengonfigurasi penyedia akan
// mencari halaman ini lagi di kemudian hari.
import AiProviderForm from "@/components/AiProviderForm";

export default function SettingsPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Setelan AI</h1>
        </div>
      </div>

      <p className="muted" style={{ maxWidth: "62ch", margin: 0 }}>
        Konfigurasi AI menggunakan skema <strong>Bring Your API Key (BYOK)</strong> yang terdiri dari{" "}
        <strong>BaseUrl</strong>, <strong>Apikey</strong>, dan <strong>Model</strong>. Anda dapat memilih preset
        penyedia AI atau memasukkan endpoint dan kredensial Anda sendiri.
      </p>

      <AiProviderForm />

      <section className="panel">
        <div className="label">Mengapa ini dapat diubah?</div>
        <ul style={{ paddingLeft: "1.1rem", margin: "0.75rem 0 0" }}>
          <li className="muted" style={{ fontSize: "0.82rem", marginBottom: "0.45rem" }}>
            Kami tidak dapat menjamin satu penyedia selalu tersedia. Jika salah
            satu sedang gangguan, Anda tetap bisa bekerja.
          </li>
          <li className="muted" style={{ fontSize: "0.82rem", marginBottom: "0.45rem" }}>
            Biaya per pemrosesan berbeda jauh antar penyedia, dan model lokal
            menghilangkan biaya per panggilan sekaligus menjaga transkrip tetap
            di komputer Anda.
          </li>
          <li className="muted" style={{ fontSize: "0.82rem" }}>
            Endpoint harus kompatibel dengan gaya OpenAI (<code>/chat/completions</code>),
            yang dipenuhi hampir semua penyedia LLM saat ini.
          </li>
        </ul>
      </section>
    </div>
  );
}
