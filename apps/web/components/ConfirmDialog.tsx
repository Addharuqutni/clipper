"use client";

// Dialog konfirmasi untuk aksi yang mengganti atau menghapus data.
//
// Bukan `window.confirm`: dialog bawaan peramban tidak bisa memuat daftar
// akibat, tidak mengikuti gaya aplikasi, dan di beberapa peramban bisa
// dimatikan pengguna ("cegah halaman ini membuat dialog") sehingga aksinya
// langsung jalan tanpa peringatan.
//
// Fokus awal ada di "Batal": Enter yang tak sengaja tidak boleh menjalankan
// aksi destruktif. Esc dan klik di luar kotak juga membatalkan.
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Kalimat pembuka: apa yang akan terjadi. */
  description: ReactNode;
  /** Akibat yang perlu diketahui sebelum menyetujui. */
  consequences?: ReactNode[];
  confirmLabel: string;
  /** `danger` untuk aksi yang tidak bisa dibatalkan (hapus). */
  tone?: "warn" | "danger";
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  consequences = [],
  confirmLabel,
  tone = "warn",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // Kembalikan fokus ke tombol pemicu saat dialog ditutup, supaya pengguna
    // keyboard tidak terlempar ke awal halaman.
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previous?.focus?.();
  }, [open]);

  if (!open) return null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    // Jebak Tab di dalam dialog: halaman di belakangnya tidak boleh terfokus.
    const focusable = boxRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled)");
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="confirm-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div
        ref={boxRef}
        className={`confirm-box confirm-${tone}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} className="confirm-title">
          {title}
        </h2>
        <div id={descId}>
          <p style={{ margin: 0 }}>{description}</p>
          {consequences.length > 0 ? (
            <ul className="confirm-list">
              {consequences.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="confirm-actions">
          <button ref={cancelRef} type="button" className="btn" onClick={onCancel}>
            Batal
          </button>
          <button type="button" className={tone === "danger" ? "btn btn-danger-solid" : "btn btn-primary"} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
