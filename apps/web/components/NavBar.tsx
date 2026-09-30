"use client";

// Navigasi utama. Dipisah jadi Client Component agar bisa menandai halaman
// aktif (usePathname) — sekaligus menjaga layout root tetap Server Component.
import Link from "next/link";
import { usePathname } from "next/navigation";

/** Ditampilkan sebagai penanda identitas. Tidak ada sesi yang dikelola. */
const LOCAL_IDENTITY_EMAIL = "local@clipper.ai";

const ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/upload", label: "Upload" },
  { href: "/youtube", label: "YouTube" },
  { href: "/settings", label: "Setelan" },
];

export function NavBar() {
  const pathname = usePathname();

  return (
    <header
      style={{
        background: "var(--paper)",
        borderBottom: "var(--bw) solid var(--ink)",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}
    >
      <div className="shell nav-bar">
        <Link
          href="/"
          aria-label="ClipperAI — beranda"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.55rem",
            textDecoration: "none",
            fontFamily: "var(--font-display)",
          }}
        >
          {/* Logo blok: teks di dalam kotak hitam. Bukan emoji, bukan raster. */}
          <span
            aria-hidden="true"
            style={{
              background: "var(--ink)",
              color: "var(--accent)",
              border: "var(--bw) solid var(--ink)",
              padding: "0.15rem 0.45rem",
              fontWeight: 900,
              fontSize: "1.05rem",
              letterSpacing: "0.02em",
              textTransform: "uppercase",
            }}
          >
            CLIP
          </span>
          <span style={{ fontWeight: 900, fontSize: "1.05rem", textTransform: "uppercase" }}>
            ClipperAI
          </span>
        </Link>

        {/* Identitas pengguna lokal. Tidak ada tombol masuk/keluar:
            aplikasi ini single-user, jadi tidak ada sesi yang dikelola. */}
        <span className="nav-identity" title={LOCAL_IDENTITY_EMAIL}>
          Lokal
        </span>

        <nav aria-label="Navigasi utama" className="nav-links">
          {ITEMS.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              // Halaman aktif ditandai blok kuning + shadow keras, bukan
              // sekadar warna teks (warna bukan satu-satunya penanda).
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className="nav-link"
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
