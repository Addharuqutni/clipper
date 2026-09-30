"use client";

// Pemilih mode reframing + pratinjau geometri.
// Memanggil /reframe/modes dan /reframe/preview di backend agar pengguna tahu
// berapa besar bar yang akan muncul SEBELUM menunggu render selesai.
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api/client";
import type { CropMode, CropModeInfo, LetterboxPreview } from "@/lib/types";

interface Props {
  /** Dimensi video sumber, bila sudah diketahui. Kosong = pratinjau dilewati. */
  sourceWidth?: number;
  sourceHeight?: number;
  value: CropMode;
  onChange: (mode: CropMode) => void;
}

export default function CropModeSelector({
  sourceWidth,
  sourceHeight,
  value,
  onChange,
}: Props) {
  const [modes, setModes] = useState<CropModeInfo[]>([]);
  const [preview, setPreview] = useState<LetterboxPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .listCropModes()
      .then((data) => {
        if (!cancelled) setModes(data.modes);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(
            e instanceof ApiError ? e.detail : "Gagal memuat daftar mode reframing.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Pratinjau hanya mungkin bila dimensi sumber diketahui. Tanpa itu,
    // permintaan akan sia-sia dan hanya menghasilkan error yang membingungkan.
    if (!sourceWidth || !sourceHeight) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    api
      .previewCrop(sourceWidth, sourceHeight, value)
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch(() => {
        // Pratinjau bersifat bantuan; kegagalannya tidak boleh menghalangi
        // pengguna memilih mode.
        if (!cancelled) setPreview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceWidth, sourceHeight, value]);

  if (loading) {
    return (
      <div className="panel">
        <div className="label">Mode reframing</div>
        <div className="skeleton" style={{ height: 60, marginTop: "0.6rem" }} />
      </div>
    );
  }

  if (error) {
    return (
      <p className="alert" role="alert">
        {error}
      </p>
    );
  }

  return (
    <div className="panel">
      <div className="label">Mode reframing</div>

      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginTop: "0.7rem" }}>
        {modes.map((mode) => {
          const active = mode.id === value;
          return (
            <label
              key={mode.id}
              style={{
                display: "flex",
                gap: "0.6rem",
                alignItems: "flex-start",
                border: "var(--bw-thin) solid var(--ink)",
                background: active ? "var(--accent)" : "var(--panel)",
                padding: "0.6rem 0.7rem",
                cursor: "pointer",
                // Penanda status TIDAK hanya warna: kotak pilihan juga
                // menampilkan tanda centang dan border lebih tebal.
                boxShadow: active ? "var(--shadow-sm)" : "none",
                borderWidth: active ? "var(--bw)" : "var(--bw-thin)",
              }}
            >
              <input
                type="radio"
                name="crop-mode"
                value={mode.id}
                checked={active}
                onChange={() => onChange(mode.id)}
                style={{ marginTop: "0.25rem" }}
              />
              <span>
                <span style={{ fontWeight: 900, display: "block" }}>
                  {mode.label}
                  {mode.requires_face_detection ? (
                    <span className="badge" style={{ marginLeft: "0.5rem" }}>
                      lebih lambat
                    </span>
                  ) : null}
                </span>
                <span className="muted" style={{ fontSize: "0.78rem" }}>
                  {mode.description}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      {preview && preview.warning ? (
        <p className="alert alert-warn" role="status" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
          {preview.warning}
        </p>
      ) : null}

      {preview ? (
        <p className="mono" style={{ fontSize: "0.75rem", margin: "0.75rem 0 0" }}>
          {preview.source_width}×{preview.source_height} → {preview.output_width}×
          {preview.output_height}
          {preview.bar_height_top > 0
            ? ` · bar atas ${preview.bar_height_top}px, bawah ${preview.bar_height_bottom}px`
            : " · tanpa bar"}
        </p>
      ) : null}
    </div>
  );
}
