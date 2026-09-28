"use client";

// Pemilih mode reframing untuk Review Studio.
//
// Dimensi sumber diambil dari metadata media job bila tersedia. Sebelumnya
// nilai 1920×1080 ditulis tetap di kode, sehingga pratinjau geometri selalu
// menampilkan angka video lanskap — menyesatkan untuk sumber vertikal atau
// persegi, dan angka bar yang ditampilkan menjadi salah.
import { useEffect, useState } from "react";
import CropModeSelector from "@/components/CropModeSelector";
import { api } from "@/lib/api/client";
import type { CropMode } from "@/lib/types";

interface Props {
  /** UUID job — dipakai untuk mengambil dimensi media yang sebenarnya. */
  jobId?: string;
  /** Mode terpilih; dipegang halaman job karena dikirim saat menekan Render. */
  value: CropMode;
  onChange: (mode: CropMode) => void;
}

export default function CropModePanel({ jobId, value, onChange }: Props) {
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    // Dimensi media baru diketahui setelah tahap ingest selesai. Bila belum
    // ada, panel tetap tampil tanpa pratinjau — lebih baik daripada menampilkan
    // angka yang salah.
    api
      .getJobMedia(jobId)
      .then((media) => {
        if (cancelled || !media) return;
        if (media.width && media.height) {
          setDimensions({ width: media.width, height: media.height });
        }
      })
      .catch(() => {
        // Metadata bersifat bantuan; kegagalannya tidak menghalangi pemilihan.
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return (
    <CropModeSelector
      sourceWidth={dimensions?.width}
      sourceHeight={dimensions?.height}
      value={value}
      onChange={onChange}
    />
  );
}
