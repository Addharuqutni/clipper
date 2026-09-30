"use client";

// Peringatan dini: penyedia AI belum dikonfigurasi.
//
// Backend menolak pembuatan job (422) bila penyedia AI belum siap, jadi tidak
// ada lagi job yang gagal di tahap analisis setelah unduhan dan transkripsi.
// Komponen ini memberi tahu SEBELUM pengguna menekan tombol dan menawarkan
// jalan keluar, alih-alih membiarkan pengguna menemukan penolakannya belakangan.
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type { SavedAiSettings } from "@/lib/types";

type Status = "loading" | "ready" | "unconfigured" | "unknown";

export default function AiReadinessNotice() {
  const [status, setStatus] = useState<Status>("loading");
  const [settings, setSettings] = useState<SavedAiSettings | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAiSettings()
      .then((saved) => {
        if (cancelled) return;
        setSettings(saved);
        setStatus(saved ? "ready" : "unconfigured");
      })
      .catch(() => {
        // Kegagalan memeriksa bukan alasan menakut-nakuti pengguna: backend
        // mungkin belum siap. Diamkan daripada menampilkan peringatan palsu.
        if (!cancelled) setStatus("unknown");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (status === "loading" || status === "ready" || status === "unknown") {
    // Belum ada peringatan saat memuat: menampilkan lalu menghilangkannya
    // terlihat seperti kedipan yang mengganggu.
    if (status === "ready" && settings) {
      const rows = [
        { k: "Penyedia", v: settings.preset },
        { k: "BaseUrl", v: settings.base_url ?? "—" },
        { k: "Model", v: settings.model ?? "—" },
        // Tanpa kunci bukan kesalahan pasti: endpoint lokal (Ollama) tidak memerlukannya.
        { k: "API key", v: settings.has_api_key ? "tersimpan" : "tidak ada" },
        { k: "Durasi maks", v: `${settings.max_video_minutes} menit` },
      ];
      return (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: "0.5rem 1.25rem",
            border: "var(--bw-thin) solid var(--ink)",
            background: "var(--panel)",
            padding: "0.55rem 0.85rem",
            fontSize: "0.82rem",
          }}
        >
          <span className="label">AI dikonfigurasi</span>
          {rows.map((r) => (
            <span key={r.k} style={{ minWidth: 0 }}>
              <span className="muted">{r.k}: </span>
              <span className="mono">{r.v}</span>
            </span>
          ))}
          <Link href="/settings" style={{ marginLeft: "auto", fontWeight: 700 }}>
            Ubah
          </Link>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="alert alert-warn" role="alert" style={{ margin: 0 }}>
      <strong>Konfigurasi AI (Bring Your API Key) belum diisi.</strong>
      <p style={{ margin: "0.4rem 0 0.6rem", fontWeight: 700 }}>
        Video belum bisa diproses sampai penyedia AI diisi di Pengaturan.
      </p>
      <Link href="/settings" className="btn btn-dark">
        Konfigurasi Bring Your API Key
      </Link>
    </div>
  );
}
