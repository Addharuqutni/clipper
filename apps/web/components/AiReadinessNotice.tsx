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
      return (
        <p className="mono" style={{ fontSize: "0.66rem", margin: 0 }}>
          Bring Your API Key: {settings.preset}
          {settings.base_url ? ` | BaseUrl: ${settings.base_url}` : ""}
          {settings.model ? ` | Model: ${settings.model}` : ""}
          {settings.has_api_key ? " (Apikey tersimpan)" : " — Apikey belum diisi"}
          {` | Durasi video maks: ${settings.max_video_minutes} menit`}
        </p>
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
