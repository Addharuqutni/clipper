"use client";

// Form penyedia AI: muat, uji, simpan, dan hapus pengaturan.
//
// Sebelumnya form ini hanya bisa MENGUJI koneksi — tidak ada tempat menyimpan
// pilihannya, sehingga pengguna harus mengetik ulang setiap kali membuka
// halaman, dan worker tidak punya cara mengetahui penyedia mana yang harus
// dipakai.
//
// **Kunci API tidak pernah ditampilkan kembali.** Server hanya mengembalikan
// penanda `has_api_key`. Field dibiarkan kosong dengan keterangan
// "(tersimpan)", dan mengosongkannya saat menyimpan berarti MEMPERTAHANKAN
// kunci lama — bukan menghapusnya. Penghapusan harus sengaja.
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api/client";
import type { AiProviderInfo, AiProviderTestResult, SavedAiSettings } from "@/lib/types";

interface Draft {
  preset: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  allowPrivate: boolean;
  direction: string;
  /** Isian manual konteks model (token). Kosong = deteksi otomatis saat simpan. */
  contextTokens: string;
}

const EMPTY: Draft = {
  preset: "gemini",
  baseUrl: "",
  model: "",
  apiKey: "",
  allowPrivate: false,
  direction: "",
  contextTokens: "",
};

export default function AiProviderForm() {
  const [providers, setProviders] = useState<AiProviderInfo[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saved, setSaved] = useState<SavedAiSettings | null>(null);
  const [result, setResult] = useState<AiProviderTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const applySaved = useCallback((settings: SavedAiSettings | null) => {
    setSaved(settings);
    if (!settings) {
      setDraft(EMPTY);
      return;
    }
    setDraft({
      preset: settings.preset,
      baseUrl: settings.base_url ?? "",
      model: settings.model ?? "",
      // Sengaja kosong: nilainya tidak pernah dikirim server ke klien.
      apiKey: "",
      allowPrivate: settings.allow_private_host,
      direction: settings.default_direction ?? "",
      // Nilai tersimpan diisi ulang supaya simpan berikutnya tidak menghapusnya.
      contextTokens: settings.context_tokens ? String(settings.context_tokens) : "",
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listProviders(), api.getAiSettings()])
      .then(([providerData, settings]) => {
        if (cancelled) return;
        setProviders(providerData.presets);
        applySaved(settings);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.detail : "Gagal memuat pengaturan.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applySaved]);

  const selected = providers.find((p) => p.id === draft.preset);

  const handlePresetChange = (presetId: string) => {
    const prov = providers.find((p) => p.id === presetId);
    setDraft((current) => ({
      ...current,
      preset: presetId,
      baseUrl: prov?.base_url ?? current.baseUrl,
      model: prov?.default_model ?? current.model,
    }));
    setResult(null);
    setError(null);
    setNotice(null);
  };

  const update = (patch: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    // Hasil uji sebelumnya tidak lagi relevan setelah konfigurasi berubah.
    setResult(null);
    setError(null);
    setNotice(null);
  };

  const payloadFor = (extra: Record<string, unknown> = {}) => ({
    preset: draft.preset,
    base_url: draft.baseUrl.trim() || null,
    model: draft.model.trim() || null,
    api_key: draft.apiKey,
    allow_private_host: draft.allowPrivate,
    default_direction: draft.direction.trim() || null,
    context_tokens: Number.parseInt(draft.contextTokens, 10) || null,
    ...extra,
  });

  const test = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.testProvider({
          preset: draft.preset,
          base_url: draft.baseUrl.trim() || undefined,
          model: draft.model.trim() || undefined,
          // Bila field kunci kosong tetapi sudah tersimpan, server memakai
          // kunci tersimpan — jadi uji tetap bermakna tanpa mengetik ulang.
          api_key: draft.apiKey,
          allow_private_host: draft.allowPrivate,
        }),
      );
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menguji penyedia.");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.saveAiSettings(payloadFor());
      applySaved(updated);
      setNotice("Pengaturan disimpan.");
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menyimpan pengaturan.");
    } finally {
      setBusy(false);
    }
  };

  const clearKey = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.saveAiSettings(payloadFor({ api_key: "", clear_api_key: true }));
      applySaved(updated);
      setNotice("Kunci API dihapus.");
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menghapus kunci.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="panel">
        <div className="label muted">Memuat pengaturan…</div>
        <div className="skeleton" style={{ height: 160, marginTop: "0.75rem" }} />
      </div>
    );
  }

  const statusLabel = saved?.has_api_key
    ? "Apikey tersimpan (isi hanya bila ingin menggantinya)"
    : "Belum ada Apikey tersimpan";

  return (
    <div className="split">
      <section className="panel">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <div className="label">Bring Your API Key (BYOK)</div>
          {saved ? (
            <span className="badge badge-done">tersimpan</span>
          ) : (
            <span className="badge badge-queued">belum disimpan</span>
          )}
        </div>

        <p className="mono" style={{ fontSize: "0.72rem", margin: "0.4rem 0 0.75rem", color: "var(--muted-ink)" }}>
          Konfigurasi AI terdiri dari <strong>BaseUrl</strong>, <strong>Apikey</strong>, dan <strong>Model</strong>.
        </p>

        <label htmlFor="preset" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          Preset Penyedia (Opsional)
        </label>
        <select
          id="preset"
          className="select"
          value={draft.preset}
          onChange={(e) => handlePresetChange(e.target.value)}
          style={{ marginTop: "0.35rem" }}
        >
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.label}
            </option>
          ))}
        </select>

        <label htmlFor="baseUrl" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          BaseUrl (URL Dasar)
        </label>
        <input
          id="baseUrl"
          className="input"
          value={draft.baseUrl}
          placeholder={selected?.base_url || "https://api.openai.com/v1"}
          onChange={(e) => update({ baseUrl: e.target.value })}
          style={{ marginTop: "0.35rem" }}
        />
        <p className="mono" style={{ fontSize: "0.66rem", margin: "0.35rem 0 0" }}>
          Endpoint LLM (mis. https://generativelanguage.googleapis.com/v1beta/openai atau https://api.openai.com/v1).
        </p>

        <label htmlFor="apiKey" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          Apikey (API Key)
        </label>
        <input
          id="apiKey"
          className="input"
          type="password"
          autoComplete="off"
          value={draft.apiKey}
          placeholder={saved?.has_api_key ? "(tersimpan)" : ""}
          onChange={(e) => update({ apiKey: e.target.value })}
          style={{ marginTop: "0.35rem" }}
        />
        <p className="mono" style={{ fontSize: "0.66rem", margin: "0.35rem 0 0" }}>
          {statusLabel}. Nilai tidak pernah dikembalikan ke browser demi keamanan.
        </p>

        <label htmlFor="model" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          Model (Nama Model)
        </label>
        <input
          id="model"
          className="input"
          value={draft.model}
          placeholder={selected?.default_model ?? "gemini-2.5-flash"}
          onChange={(e) => update({ model: e.target.value })}
          style={{ marginTop: "0.35rem" }}
        />
        <p className="mono" style={{ fontSize: "0.66rem", margin: "0.35rem 0 0" }}>
          Nama model LLM yang ingin digunakan (mis. gemini-2.5-flash, gpt-4o-mini, llama-3.3-70b-versatile).
        </p>

        <label
          style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start", marginTop: "0.85rem", cursor: "pointer" }}
        >
          <input
            type="checkbox"
            checked={draft.allowPrivate}
            onChange={(e) => update({ allowPrivate: e.target.checked })}
            style={{ marginTop: "0.25rem" }}
          />
          <span className="muted" style={{ fontSize: "0.78rem" }}>
            Izinkan alamat lokal/privat (untuk Ollama atau model di jaringan sendiri)
          </span>
        </label>

        <label htmlFor="direction" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          Arahan tetap (opsional)
        </label>
        <textarea
          id="direction"
          className="textarea"
          value={draft.direction}
          rows={3}
          placeholder="mis. utamakan momen lucu dan hindari bagian promosi"
          onChange={(e) => update({ direction: e.target.value })}
          style={{ marginTop: "0.35rem" }}
        />
        <p className="mono" style={{ fontSize: "0.66rem", margin: "0.35rem 0 0" }}>
          Ditambahkan ke setiap permintaan skoring.
        </p>

        <label htmlFor="contextTokens" className="label" style={{ display: "block", marginTop: "0.85rem" }}>
          Konteks model (token, opsional)
        </label>
        <input
          id="contextTokens"
          className="input"
          type="number"
          min={1024}
          inputMode="numeric"
          value={draft.contextTokens}
          placeholder="otomatis"
          onChange={(e) => update({ contextTokens: e.target.value })}
          style={{ marginTop: "0.35rem" }}
        />
        <p className="mono" style={{ fontSize: "0.66rem", margin: "0.35rem 0 0" }}>
          Kosongkan agar dideteksi dari penyedia saat Simpan. Isi hanya bila
          penyedia tidak melaporkannya. Menentukan durasi video maksimum.
        </p>

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", flexWrap: "wrap" }}>
          <button className="btn btn-primary" type="button" onClick={save} disabled={busy} aria-busy={busy}>
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
          <button className="btn" type="button" onClick={test} disabled={busy}>
            Uji koneksi
          </button>
          {saved?.has_api_key ? (
            <button className="btn" type="button" onClick={clearKey} disabled={busy}>
              Hapus kunci
            </button>
          ) : null}
        </div>
      </section>

      <aside className="panel">
        <div className="label">Hasil</div>

        {error ? (
          <p className="alert" role="alert" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            {error}
          </p>
        ) : null}

        {notice ? (
          <p className="alert alert-info" role="status" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            {notice}
          </p>
        ) : null}

        {result ? (
          <div role="status" style={{ marginTop: "0.75rem" }}>
            <p className={result.ok ? "alert alert-ok" : "alert"} style={{ margin: 0 }}>
              <span className="label">{result.ok ? "Berhasil" : "Gagal"}</span>
              <br />
              <span style={{ fontSize: "0.82rem" }}>{result.message}</span>
            </p>

            {result.hint ? (
              <p className="alert alert-warn" role="status" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
                {result.hint}
              </p>
            ) : null}

            <dl style={{ margin: "0.85rem 0 0" }}>
              <Detail label="Model" value={result.resolved_model || "—"} />
              <Detail label="URL" value={result.resolved_base_url || "—"} />
              <Detail label="Latensi" value={result.latency_ms !== null ? `${result.latency_ms} ms` : "—"} />
            </dl>

            {result.sample ? (
              <p className="mono" style={{ fontSize: "0.72rem", marginTop: "0.75rem" }}>
                jawaban: {result.sample}
              </p>
            ) : null}
          </div>
        ) : null}

        {!result && !error && !notice ? (
          <p className="muted" style={{ fontSize: "0.82rem", marginTop: "0.75rem" }}>
            Tekan “Uji koneksi” untuk memastikan konfigurasi bekerja, lalu
            “Simpan” agar dipakai saat memproses video.
          </p>
        ) : null}

        {saved ? (
          <dl style={{ margin: "1rem 0 0" }}>
            <Detail
              label="Konteks model"
              value={
                saved.context_tokens
                  ? `${saved.context_tokens.toLocaleString("id-ID")} token`
                  : "tidak dilaporkan (asumsi 128.000 token)"
              }
            />
            <Detail label="Durasi video maks." value={`${saved.max_video_minutes} menit`} />
          </dl>
        ) : null}

        {saved?.updated_at ? (
          <p className="mono" style={{ fontSize: "0.66rem", marginTop: "1rem", marginBottom: 0 }}>
            terakhir diubah: {new Date(saved.updated_at).toLocaleString("id-ID")}
          </p>
        ) : null}
      </aside>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "0.75rem",
        borderTop: "var(--bw-thin) solid var(--line)",
        padding: "0.4rem 0",
      }}
    >
      <dt className="muted" style={{ fontSize: "0.72rem", margin: 0 }}>
        {label}
      </dt>
      <dd
        className="mono"
        style={{ fontSize: "0.7rem", margin: 0, fontWeight: 900, textAlign: "right", wordBreak: "break-all" }}
      >
        {value}
      </dd>
    </div>
  );
}
