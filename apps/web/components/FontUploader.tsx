"use client";

// Unggah dan kelola font kustom (.ttf/.otf).
//
// **Mengapa ini bagian dari panel gaya, bukan halaman terpisah.** Font hanya
// berguna bersama gaya teks, dan pengguna yang ingin memakai font baru harus
// melihatnya langsung muncul di daftar font di sebelahnya. Halaman terpisah
// memaksa perpindahan konteks yang tidak menambah apa pun.
import { useRef, useState } from "react";
import { ApiError, api, uploadFile } from "@/lib/api/client";
import type { FontAsset } from "@/lib/types";

interface Props {
  fonts: FontAsset[];
  /** Dipanggil setelah unggah/hapus berhasil agar induk memuat ulang. */
  onChanged: () => void;
}

export default function FontUploader({ fonts, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const created = await uploadFile<FontAsset>("/fonts", file);
      setMessage(`Font "${created.family}" ditambahkan.`);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Unggah font gagal.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const remove = async (font: FontAsset) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.deleteFont(font.id);
      setMessage(`Font "${font.family}" dihapus.`);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Hapus font gagal.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginBottom: "0.8rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <label className="btn" style={{ cursor: busy ? "wait" : "pointer", fontSize: "0.7rem" }}>
          {busy ? "Memproses…" : "Unggah font (.ttf/.otf)"}
          <input
            ref={inputRef}
            type="file"
            accept=".ttf,.otf,.ttc,font/ttf,font/otf"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
            }}
            style={{ display: "none" }}
          />
        </label>
        <span className="hint" style={{ margin: 0 }}>
          Maks 20 MB
        </span>
      </div>

      {error ? (
        <p className="alert" role="alert" style={{ fontSize: "0.7rem", marginTop: "0.4rem" }}>
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="hint" role="status" style={{ marginTop: "0.4rem" }}>
          {message}
        </p>
      ) : null}

      {fonts.length ? (
        <div style={{ marginTop: "0.4rem" }}>
          {fonts.map((font) => (
            <div
              key={font.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.4rem",
                fontSize: "0.7rem",
                padding: "0.15rem 0",
              }}
            >
              <span style={{ flex: 1 }}>{font.family}</span>
              <span className="muted mono" style={{ fontSize: "0.75rem" }}>
                {(font.size_bytes / 1024).toFixed(0)} KB
              </span>
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => void remove(font)}
                style={{ fontSize: "0.72rem", padding: "0.1rem 0.4rem" }}
                aria-label={`Hapus font ${font.family}`}
              >
                Hapus
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
