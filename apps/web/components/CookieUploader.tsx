"use client";

// Unggah cookies.txt YouTube.
//
// Cookies yang sah disimpan server TERENKRIPSI dan dipakai untuk unduhan
// berikutnya. Isinya tidak pernah dikembalikan API — yang kembali hanya nama
// cookie yang ditemukan. Cookies YouTube setara kredensial sesi penuh.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api/client";
import type { CookieRequirements, CookieValidationResult } from "@/lib/types";

export default function CookieUploader() {
  const [result, setResult] = useState<CookieValidationResult | null>(null);
  const [requirements, setRequirements] = useState<CookieRequirements | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stored, setStored] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.cookieStatus().then((s) => setStored(s.stored)).catch(() => undefined);
  }, []);

  const removeStored = async () => {
    setBusy(true);
    try {
      await api.deleteCookies();
      setStored(false);
      setResult(null);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menghapus cookies.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    api
      .cookieRequirements()
      .then(setRequirements)
      .catch(() => {
        // Bantuan bersifat opsional; kegagalannya tidak menghalangi unggah.
        setRequirements(null);
      });
  }, []);

  const onFile = useCallback(async (file: File | undefined) => {
    if (!file) return;

    // Penjagaan sisi klien: backend juga memeriksa, tetapi menolak lebih awal
    // menghemat unggahan berkas besar yang pasti ditolak.
    if (!file.name.endsWith(".txt")) {
      setError("Berkas harus berekstensi .txt (cookies.txt format Netscape).");
      setResult(null);
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const outcome = await api.uploadCookies(file);
      setResult(outcome);
      if (outcome.stored) setStored(true);
    } catch (e: unknown) {
      setResult(null);
      setError(e instanceof ApiError ? e.detail : "Gagal mengunggah cookies.");
    } finally {
      setBusy(false);
      // Kosongkan input agar berkas yang sama bisa dipilih ulang.
      if (inputRef.current) inputRef.current.value = "";
    }
  }, []);

  return (
    <div className="panel">
      <div className="label">Cookies YouTube (opsional)</div>

      <p className="muted" style={{ fontSize: "0.8rem", margin: "0.5rem 0 0.85rem" }}>
        Diperlukan untuk video yang meminta login atau dibatasi usia. Tanpa
        cookies, unduhan pada video seperti itu akan gagal.
      </p>

      <input
        ref={inputRef}
        id="cookies-file"
        type="file"
        accept=".txt,text/plain"
        onChange={(e) => void onFile(e.target.files?.[0])}
        disabled={busy}
        style={{ display: "none" }}
      />
      <label
        htmlFor="cookies-file"
        className="btn"
        style={{ opacity: busy ? 0.6 : 1, pointerEvents: busy ? "none" : "auto" }}
      >
        {busy ? "Memeriksa…" : stored ? "Ganti cookies.txt" : "Pilih cookies.txt"}
      </label>
      {stored ? (
        <>
          <span className="badge badge-done" style={{ marginLeft: "0.5rem" }}>Tersimpan</span>
          <button type="button" className="btn" style={{ marginLeft: "0.5rem" }} disabled={busy} onClick={() => void removeStored()}>
            Hapus
          </button>
        </>
      ) : null}

      {error ? (
        <p className="alert" role="alert" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
          {error}
        </p>
      ) : null}

      {result ? (
        <div style={{ marginTop: "0.85rem" }} role="status">
          <p
            className={result.valid ? "alert alert-ok" : "alert"}
            style={{ margin: 0 }}
          >
            <span className="label">
              {result.valid ? "Cookies diterima dan disimpan" : "Cookies ditolak"}
            </span>
            {result.valid ? (
              <>
                <br />
                <span className="mono" style={{ fontSize: "0.72rem" }}>
                  ditemukan: {result.found.join(", ")}
                </span>
              </>
            ) : (
              <>
                <br />
                <span style={{ fontSize: "0.78rem" }}>{result.message}</span>
              </>
            )}
          </p>

          {result.warning ? (
            <p className="alert alert-warn" role="status" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
              {result.warning}
            </p>
          ) : null}
        </div>
      ) : null}

      {requirements ? (
        <details style={{ marginTop: "1rem" }}>
          <summary className="label" style={{ cursor: "pointer" }}>
            Cara mendapatkan cookies
          </summary>
          <p className="muted" style={{ fontSize: "0.78rem", marginTop: "0.5rem" }}>
            {requirements.explanation}
          </p>
          <p className="mono" style={{ fontSize: "0.68rem", margin: 0 }}>
            minimal salah satu: {requirements.required_any_of.join(", ")}
          </p>
          <p className="mono" style={{ fontSize: "0.68rem", margin: "0.3rem 0 0" }}>
            ukuran maks: {Math.round(requirements.max_file_bytes / 1024)} KB
          </p>
        </details>
      ) : null}

      <p className="mono" style={{ fontSize: "0.66rem", marginTop: "0.85rem", marginBottom: 0 }}>
        Disimpan terenkripsi di komputer ini; isinya tidak pernah dikembalikan atau dicatat.
      </p>
    </div>
  );
}
