import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/components/NavBar";

export const metadata: Metadata = {
  title: "ClipperAI",
  description: "Ubah video panjang menjadi klip vertikal 9:16.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Layout kolom setinggi layar: header (sticky) + main (mengisi sisa) +
    // footer. `main { flex: 1 }` yang mendorong footer ke bawah, sehingga
    // pada halaman pendek footer tetap menempel di dasar viewport alih-alih
    // menggantung di tengah layar. `min-height: 100dvh` dipakai (bukan 100vh)
    // karena satuan dvh menyesuaikan diri saat address bar browser seluler
    // muncul/hilang, sehingga footer tidak terpotong.
    <html lang="id">
      <body
        style={{
          display: "flex",
          flexDirection: "column",
          minHeight: "100dvh",
        }}
      >
        <NavBar />

        <main
          className="shell"
          style={{
            flex: 1,
            paddingTop: "2rem",
            paddingBottom: "3rem",
            width: "100%",
          }}
        >
          {children}
        </main>

        <footer
          style={{
            borderTop: "var(--bw) solid var(--ink)",
            background: "var(--ink)",
            color: "var(--paper)",
            padding: "1.25rem",
          }}
        >
          <div className="shell label" style={{ display: "flex", flexWrap: "wrap", gap: "1rem" }}>
            <span>ClipperAI</span>
            <span>Berjalan lokal</span>
            <span>Download-only</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
