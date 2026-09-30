"use client";

// Panel kustomisasi gaya teks: pilih preset, setel properti, lihat pratinjau.
//
// **Alur penyimpanan yang disengaja.** Perubahan ditahan di state lokal
// (pratinjau bergerak seketika), lalu pengguna menekan "Terapkan ke video ini"
// atau "Simpan sebagai preset". Menyimpan setiap pergerakan slider akan
// mengirim puluhan permintaan per detik; menyimpan hanya di akhir tanpa
// memberi tahu juga berbahaya — karena itu status kotor/tersimpan selalu
// terlihat.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api/client";
import type { CaptionPreset, CaptionStyle, CaptionStyleOptions, FontAsset } from "@/lib/types";
import CaptionPreview from "@/components/CaptionPreview";
import FontUploader from "@/components/FontUploader";

interface Props {
  jobId: string;
}

/** Kontrol slider + angka yang dipakai berulang, agar tidak disalin berkali-kali. */
function SliderRow({
  label, value, min, max, step, onChange, hint,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  hint?: string;
}) {
  return (
    <div style={{ marginBottom: "0.7rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <label className="label" htmlFor={`cap-${label}`}>
          {label}
        </label>
        <span className="mono" style={{ fontSize: "0.78rem", fontWeight: 900 }}>
          {value}
        </span>
      </div>
      <input
        id={`cap-${label}`}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ width: "100%" }}
      />
      {hint ? (
        <p className="hint" style={{ margin: "0.1rem 0 0" }}>{hint}</p>
      ) : null}
    </div>
  );
}

/** Pemilih warna + nilai hex, karena color picker saja sulit dibaca presisinya. */
function ColorRow({
  label, value, onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "0.5rem",
        marginBottom: "0.55rem",
      }}
    >
      <label className="label" htmlFor={`col-${label}`}>
        {label}
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <input
          id={`col-${label}`}
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          style={{
            width: 34,
            height: 26,
            border: "var(--bw-thin) solid var(--line)",
            cursor: "pointer",
          }}
        />
        <span className="mono" style={{ fontSize: "0.75rem", minWidth: "4.4rem" }}>
          {value}
        </span>
      </div>
    </div>
  );
}

export default function CaptionStylePanel({ jobId }: Props) {
  const [options, setOptions] = useState<CaptionStyleOptions | null>(null);
  const [style, setStyle] = useState<CaptionStyle | null>(null);
  const [presets, setPresets] = useState<CaptionPreset[]>([]);
  const [fonts, setFonts] = useState<FontAsset[]>([]);
  const [source, setSource] = useState<"job" | "preset" | "default">("default");
  const [presetName, setPresetName] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newPresetName, setNewPresetName] = useState("");

  const load = useCallback(async () => {
    try {
      const [opts, current, presetList, fontList] = await Promise.all([
        api.captionOptions(),
        api.getJobCaptionStyle(jobId),
        api.listCaptionPresets(),
        api.listFonts(),
      ]);
      setOptions(opts);
      setStyle(current.style);
      setSource(current.source);
      setPresetName(current.preset_name);
      setPresets(presetList.items);
      setFonts(fontList.items);
      setDirty(false);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat pengaturan gaya.");
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = (changes: Partial<CaptionStyle>) => {
    setStyle((prev) => (prev ? { ...prev, ...changes } : prev));
    setDirty(true);
    setMessage(null);
  };

  const applyToJob = async () => {
    if (!style) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.setJobCaptionStyle(jobId, style);
      setStyle(result.style);
      setSource(result.source);
      setPresetName(result.preset_name);
      setDirty(false);
      setMessage("Gaya diterapkan ke video ini.");
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menerapkan gaya.");
    } finally {
      setBusy(false);
    }
  };

  const clearOverride = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.setJobCaptionStyle(jobId, null);
      setStyle(result.style);
      setSource(result.source);
      setPresetName(result.preset_name);
      setDirty(false);
      setMessage("Override dihapus; kembali mengikuti preset bawaan.");
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menghapus override.");
    } finally {
      setBusy(false);
    }
  };

  const saveAsPreset = async () => {
    if (!style || !newPresetName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCaptionPreset(newPresetName.trim(), style);
      setPresets((prev) => [...prev, created]);
      setNewPresetName("");
      setMessage(`Preset "${created.name}" disimpan.`);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menyimpan preset.");
    } finally {
      setBusy(false);
    }
  };

  // BUKAN React Hook: ini handler biasa. Nama berawalan "use" membuat aturan
  // `react-hooks/rules-of-hooks` menganggapnya hook dan menolak pemanggilannya
  // dari `onChange`. Diberi nama `loadPreset` agar niatnya jelas.
  const loadPreset = (preset: CaptionPreset) => {
    setStyle(preset.style);
    setDirty(true);
    setMessage(`Preset "${preset.name}" dimuat. Tekan Terapkan untuk memakainya.`);
  };

  const makeDefault = async (preset: CaptionPreset) => {
    setBusy(true);
    try {
      const list = await api.setDefaultCaptionPreset(preset.id);
      setPresets(list.items);
      setMessage(`"${preset.name}" kini preset bawaan untuk video berikutnya.`);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menetapkan preset bawaan.");
    } finally {
      setBusy(false);
    }
  };

  const removePreset = async (preset: CaptionPreset) => {
    setBusy(true);
    try {
      await api.deleteCaptionPreset(preset.id);
      setPresets((prev) => prev.filter((p) => p.id !== preset.id));
      setMessage(`Preset "${preset.name}" dihapus.`);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menghapus preset.");
    } finally {
      setBusy(false);
    }
  };

  const fontChoices = useMemo(() => {
    const system = ["Arial Black", "Arial", "Impact", "Verdana", "Georgia", "Courier New"];
    // Font kustom didahulukan: pengguna yang mengunggahnya hampir pasti ingin
    // memakainya, dan daftar font sistem panjang membuatnya tenggelam.
    return [...fonts.map((f) => f.family), ...system];
  }, [fonts]);

  if (!style || !options) {
    return (
      <div className="panel" role="status">
        <span className="label muted">{error ?? "Memuat gaya teks…"}</span>
      </div>
    );
  }

  return (
    <div>
      {error ? (
        <p className="alert" role="alert" style={{ marginBottom: "0.85rem" }}>
          {error}
        </p>
      ) : null}
      {message ? (
        <p
          className="alert alert-ok"
          role="status"
          style={{ marginBottom: "0.85rem" }}
        >
          {message}
        </p>
      ) : null}

      {/* split-lead: pratinjau 9:16 di kolom sempit (--aside-w). Di kolom 1fr
          tingginya ikut lebar layar sehingga kotak pratinjau menjadi raksasa. */}
      <div className="split split-lead">
        {/* Hanya pratinjau yang menempel saat digulir. Jika seluruh kolom
            (pratinjau + "Asal gaya") yang sticky, tingginya melebihi ruang di
            bawah header, sehingga di akhir gulir bagian atasnya terdorong ke
            balik header. Section direntangkan setinggi baris agar pratinjau
            punya ruang menempel sepanjang kolom kontrol. */}
        <section style={{ position: "static", alignSelf: "stretch" }}>
          <div className="panel" style={{ marginBottom: "0.85rem" }}>
            <div className="label">Asal gaya</div>
            <div className="mono" style={{ fontSize: "0.72rem", marginTop: "0.25rem" }}>
              {source === "job"
                ? "khusus video ini"
                : source === "preset"
                  ? `preset "${presetName ?? "-"}"`
                  : "bawaan sistem"}
            </div>
            <p className="hint" style={{ marginTop: "0.35rem", marginBottom: 0 }}>
              {source === "job"
                ? "Gaya ini hanya berlaku pada video ini."
                : "Gaya ini berlaku pada semua video yang tidak punya pengaturan sendiri."}
            </p>
          </div>

          <div style={{ position: "sticky", top: "6rem" }}>
            <CaptionPreview style={style} />
          </div>
        </section>

        <section className="panel">
          <div className="label">Gaya teks</div>

          <select
            className="input"
            value=""
            onChange={(e) => {
              const found = presets.find((p) => p.id === e.target.value);
              if (found) loadPreset(found);
            }}
            aria-label="Muat preset gaya"
            style={{ marginTop: "0.6rem" }}
          >
            <option value="">Muat preset…</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_default ? " (bawaan)" : ""}
              </option>
            ))}
          </select>

          {presets.map((p) => (
            <div
              key={p.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.4rem",
                padding: "0.25rem 0",
                borderBottom: "1px solid var(--muted-bg)",
              }}
            >
              <span style={{ flex: 1, fontSize: "0.76rem", fontWeight: p.is_default ? 900 : 400 }}>
                {p.name}
              </span>
              {p.is_default ? (
                <span className="badge">bawaan</span>
              ) : (
                <button
                  className="btn"
                  type="button"
                  disabled={busy}
                  onClick={() => void makeDefault(p)}
                  style={{ fontSize: "0.72rem", padding: "0.15rem 0.45rem" }}
                >
                  Jadikan bawaan
                </button>
              )}
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => void removePreset(p)}
                style={{ fontSize: "0.72rem", padding: "0.15rem 0.45rem" }}
                aria-label={`Hapus preset ${p.name}`}
              >
                Hapus
              </button>
            </div>
          ))}

          <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.6rem" }}>
            <input
              className="input"
              value={newPresetName}
              onChange={(e) => setNewPresetName(e.target.value)}
              placeholder="Nama preset baru…"
              style={{ flex: 1, fontSize: "0.78rem" }}
              aria-label="Nama preset baru"
            />
            <button
              className="btn"
              type="button"
              onClick={() => void saveAsPreset()}
              disabled={!newPresetName.trim() || busy}
            >
              Simpan
            </button>
          </div>

          <hr
            style={{
              margin: "1rem 0",
              border: "none",
              borderTop: "var(--bw-thin) solid var(--line)",
            }}
          />

          <label className="label" htmlFor="cap-font">
            Font
          </label>
          <select
            id="cap-font"
            className="input"
            value={style.font_name}
            onChange={(e) => patch({ font_name: e.target.value })}
            style={{ marginTop: "0.3rem", marginBottom: "0.7rem" }}
          >
            {fontChoices.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>

          <FontUploader
            fonts={fonts}
            onChanged={() => {
              // Hanya daftar font yang dimuat ulang: load() penuh akan
              // membuang perubahan gaya yang belum disimpan.
              void api.listFonts().then((list) => setFonts(list.items)).catch(() => undefined);
            }}
          />

          <label className="label" htmlFor="cap-anim">
            Animasi
          </label>
          <select
            id="cap-anim"
            className="input"
            value={style.animation}
            onChange={(e) => patch({ animation: e.target.value as CaptionStyle["animation"] })}
            style={{ marginTop: "0.3rem", marginBottom: "0.8rem" }}
          >
            {options.animations.map((a) => (
              <option key={a} value={a}>
                {a === "none"
                  ? "none — tanpa gerakan"
                  : a === "karaoke"
                    ? "karaoke — sorot kata"
                    : a === "bounce"
                      ? "bounce — memantul"
                      : a === "pop"
                        ? "pop — muncul membesar"
                        : "fade — memudar masuk"}
              </option>
            ))}
          </select>

          {style.animation === "bounce" || style.animation === "pop" ? (
            <SliderRow
              label="Skala animasi (×100)"
              value={Math.round(style.animation_scale * 100)}
              min={100}
              max={200}
              step={5}
              onChange={(v) => patch({ animation_scale: v / 100 })}
              hint="Seberapa besar teks muncul sebelum mengendur."
            />
          ) : null}

          <SliderRow
            label="Ukuran font"
            value={style.font_size}
            min={options.font_size_min}
            max={200}
            step={1}
            onChange={(v) => patch({ font_size: v })}
          />
          <SliderRow
            label="Kata per baris"
            value={style.chunk_size}
            min={1}
            max={8}
            step={1}
            onChange={(v) => patch({ chunk_size: v })}
            hint="Berapa kata tampil sekaligus."
          />
          <SliderRow
            label="Posisi dari bawah"
            value={style.margin_vertical}
            min={0}
            max={900}
            step={10}
            onChange={(v) => patch({ margin_vertical: v })}
            hint="Perbesar agar teks naik, menjauh dari tombol platform."
          />
          <SliderRow
            label="Outline"
            value={style.outline}
            min={0}
            max={12}
            step={1}
            onChange={(v) => patch({ outline: v })}
          />
          <SliderRow
            label="Bayangan"
            value={style.shadow}
            min={0}
            max={12}
            step={1}
            onChange={(v) => patch({ shadow: v })}
          />

          <div style={{ display: "flex", gap: "1rem", margin: "0.4rem 0 0.9rem" }}>
            <label style={{ display: "flex", gap: "0.35rem", alignItems: "center", fontSize: "0.76rem" }}>
              <input
                type="checkbox"
                checked={style.bold}
                onChange={(e) => patch({ bold: e.target.checked })}
              />
              Tebal
            </label>
            <label style={{ display: "flex", gap: "0.35rem", alignItems: "center", fontSize: "0.76rem" }}>
              <input
                type="checkbox"
                checked={style.italic}
                onChange={(e) => patch({ italic: e.target.checked })}
              />
              Miring
            </label>
            <label style={{ display: "flex", gap: "0.35rem", alignItems: "center", fontSize: "0.76rem" }}>
              <input
                type="checkbox"
                checked={style.border_style === 3}
                onChange={(e) => patch({ border_style: e.target.checked ? 3 : 1 })}
              />
              Kotak latar
            </label>
          </div>

          <div className="label">Warna</div>
          <div style={{ marginTop: "0.4rem" }}>
            <ColorRow
              label="Teks"
              value={style.primary_rgb}
              onChange={(v) => patch({ primary_rgb: v })}
            />
            <ColorRow
              label="Sorot kata"
              value={style.highlight_rgb}
              onChange={(v) => patch({ highlight_rgb: v })}
            />
            <ColorRow
              label="Outline"
              value={style.outline_rgb}
              onChange={(v) => patch({ outline_rgb: v })}
            />
            {style.border_style === 3 ? (
              <>
                <ColorRow
                  label="Kotak latar"
                  value={style.back_rgb}
                  onChange={(v) => patch({ back_rgb: v })}
                />
                <SliderRow
                  label="Kepekatan kotak"
                  value={style.back_alpha}
                  min={0}
                  max={100}
                  step={5}
                  onChange={(v) => patch({ back_alpha: v })}
                  hint="0 = tembus, 100 = pekat."
                />
              </>
            ) : null}
          </div>

          {dirty ? (
            <p
              className="hint"
              style={{ color: "var(--ink)", margin: "0.6rem 0 0" }}
              role="status"
            >
              Ada perubahan yang belum diterapkan.
            </p>
          ) : null}

          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.8rem", flexWrap: "wrap" }}>
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => void applyToJob()}
              disabled={busy || !dirty}
            >
              {busy ? "Menyimpan…" : "Terapkan ke video ini"}
            </button>
            {source === "job" ? (
              <button className="btn" type="button" onClick={() => void clearOverride()} disabled={busy}>
                Kembali ke preset
              </button>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
