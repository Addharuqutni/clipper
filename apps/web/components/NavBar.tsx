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
      <div
        className="shell"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "1rem",
          flexWrap: "wrap",
          paddingTop: "0.75rem",
          paddingBottom: "0.75rem",
        }}
      >
        <Link
          href="/"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.55rem",
            textDecoration: "none",
          }}
        >
          {/* Logo blok: teks di dalam kotak hitam. Bukan emoji, bukan raster. */}
          <span
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

        <nav
          aria-label="Navigasi utama"
          style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}
        >
          {ITEMS.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className="label"
                style={{
                  textDecoration: "none",
                  border: "var(--bw-thin) solid var(--ink)",
                  padding: "0.45rem 0.7rem",
                  // Halaman aktif ditandai blok kuning + shadow keras,
                  // bukan sekadar perubahan warna teks (aksesibilitas:
                  // warna bukan satu-satunya penanda).
                  background: active ? "var(--accent)" : "var(--panel)",
                  boxShadow: active ? "var(--shadow-sm)" : "none",
                  fontWeight: 900,
                }}
              >
                {item.label}
              </Link>
            );
          })}

          {/* Identitas pengguna lokal. Tidak ada tombol masuk/keluar:
              aplikasi ini single-user, jadi tidak ada sesi yang dikelola. */}
          <span className="label muted" title={LOCAL_IDENTITY_EMAIL}>
            lokal
          </span>
        </nav>
      </div>
    </header>
  );
}
