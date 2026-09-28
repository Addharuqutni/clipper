"use client";

// Timeline overlay B-roll: kelola pustaka aset dan penempatannya pada segmen.
//
// **Mengapa timeline, bukan daftar biasa.** Overlay adalah objek BERWAKTU:
// "grafik muncul pada detik 3 sampai 6" hanya bermakna bila terlihat relatif
// terhadap durasi segmen. Batang waktu membuat tumpang tindih, celah, dan
// posisi langsung terbaca — daftar angka memaksa pengguna menghitung sendiri.
//
// Skala waktu di sini RELATIF terhadap segmen (bukan media panjang). Itu
// disengaja dan sama dengan yang disimpan backend: pengguna menempatkan overlay
// pada potongan yang akan diunggah, bukan pada menit ke-N video sumber.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, uploadFile } from "@/lib/api/client";
import type {
  OverlayAsset,
  OverlayOptions,
  OverlayPlacement,
  OverlayPosition,
  OverlayTransition,
} from "@/lib/types";

interface Props {
  segmentId: string;
  /** Durasi segmen, untuk skala timeline dan validasi. */
  durationS: number;
}

/** Warna per jenis aset, agar kategori terbaca sekilas di timeline. */
const KIND_COLOR: Record<string, string> = {
  image: "var(--toxic)",
  video: "var(--accent)",
  audio: "#9ad0ff",
};

export default function OverlayTimeline({ segmentId, durationS }: Props) {
  const [assets, setAssets] = useState<OverlayAsset[]>([]);
  const [options, setOptions] = useState<OverlayOptions | null>(null);
  const [placements, setPlacements] = useState<OverlayPlacement[]>([]);
  const [selected, setSelected] = useState<OverlayPlacement | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  // Salinan lokal overlay terpilih untuk input. Server hanya disentuh saat
  // input dilepas (blur / lepas slider): PATCH per ketikan membuat field
  // meloncat balik dan respons yang tiba tidak berurutan menyimpan nilai basi.
  const [draft, setDraft] = useState<OverlayPlacement | null>(null);
  const [startText, setStartText] = useState("");
  const [endText, setEndText] = useState("");
  const patchSeq = useRef(0);
  // Id unik per komponen: satu halaman memuat timeline untuk banyak segmen.
  const fid = (name: string) => `${segmentId}-ov-${name}`;

  useEffect(() => {
    setDraft(selected);
    setStartText(selected ? String(selected.start_s) : "");
    setEndText(selected ? String(selected.end_s) : "");
  }, [selected]);

  const load = useCallback(async () => {
    try {
      const [assetList, opts, placementList] = await Promise.all([
        api.listOverlayAssets(),
        api.overlayOptions(),
        api.listOverlays(segmentId),
      ]);
      setAssets(assetList.items);
      setOptions(opts);
      setPlacements(placementList.items);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat overlay.");
    }
  }, [segmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await uploadFile("/overlays/assets", file, {
        name: uploadName.trim() || file.name,
        tags: uploadTags,
        default_position: "top_right",
        default_scale: "0.35",
      });
      setUploadName("");
      setUploadTags("");
      setMessage("Aset ditambahkan ke pustaka.");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unggah aset gagal.");
    } finally {
      setBusy(false);
    }
  };

  const place = async (asset: OverlayAsset) => {
    setBusy(true);
    setError(null);
    try {
      // Ditaruh di detik 0 dengan durasi bawaan 3 detik (atau durasi segmen
      // bila lebih pendek) — pengguna lalu menggesernya di timeline.
      const span = Math.min(3, Math.max(0.5, durationS));
      const created = await api.createOverlay(segmentId, {
        asset_id: asset.id,
        start_s: 0,
        end_s: span,
      });
      setPlacements((prev) => [...prev, created]);
      setSelected(created);
      setMessage(`"${asset.name}" ditempatkan; atur waktunya di bawah.`);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menempatkan overlay.");
    } finally {
      setBusy(false);
    }
  };

  const suggest = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.suggestOverlays(segmentId);
      await load();
      setMessage(
        result.created.length
          ? `${result.created.length} usulan dibuat (kata kunci: ${result.matched_tags.join(", ")}). Tinjau sebelum dirender.`
          : "Tidak ada kata kunci yang cocok di segmen ini. Tambahkan tag pada aset, atau tempatkan manual.",
      );
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal membuat usulan.");
    } finally {
      setBusy(false);
    }
  };

  const patchSelected = async (changes: Partial<OverlayPlacement>) => {
    if (!selected) return;
    const changed = Object.fromEntries(
      Object.entries(changes).filter(([key, value]) => selected[key as keyof OverlayPlacement] !== value),
    ) as Partial<OverlayPlacement>;
    if (!Object.keys(changed).length) return;
    const seq = ++patchSeq.current;
    try {
      const updated = await api.updateOverlay(selected.id, changed);
      setPlacements((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      // Hanya respons terbaru yang boleh menentukan nilai di layar.
      if (seq === patchSeq.current) setSelected(updated);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal mengubah overlay.");
      if (seq === patchSeq.current) setSelected({ ...selected });
    }
  };

  /** Simpan field waktu saat input dilepas; teks kosong/tidak sah dikembalikan. */
  const commitTime = (field: "start_s" | "end_s", text: string) => {
    const value = Number(text);
    if (text.trim() === "" || !Number.isFinite(value)) {
      if (field === "start_s") setStartText(String(selected?.start_s ?? ""));
      else setEndText(String(selected?.end_s ?? ""));
      return;
    }
    void patchSelected({ [field]: value });
  };

  const remove = async (placement: OverlayPlacement) => {
    setBusy(true);
    try {
      await api.deleteOverlay(placement.id);
      setPlacements((prev) => prev.filter((p) => p.id !== placement.id));
      if (selected?.id === placement.id) setSelected(null);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menghapus overlay.");
    } finally {
      setBusy(false);
    }
  };

  const removeAsset = async (asset: OverlayAsset) => {
    setBusy(true);
    try {
      await api.deleteOverlayAsset(asset.id);
      setMessage(`Aset "${asset.name}" dihapus beserta penempatannya.`);
      setSelected(null);
      await load();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Gagal menghapus aset.");
    } finally {
      setBusy(false);
    }
  };

  // Lebar blok timeline dalam persen durasi segmen.
  const pct = useCallback(
    (seconds: number) => (durationS > 0 ? Math.min(100, (seconds / durationS) * 100) : 0),
    [durationS],
  );

  const tickMarks = useMemo(() => {
    if (durationS <= 0) return [];
    // ~6 penanda, dibulatkan agar angkanya enak dibaca.
    const step = Math.max(1, Math.round(durationS / 6));
    const marks: number[] = [];
    for (let t = 0; t <= durationS; t += step) marks.push(t);
    return marks;
  }, [durationS]);

  return (
    <div className="panel" style={{ marginTop: "0.85rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <span className="label">Overlay &amp; B-roll</span>
        <button
          className="btn"
          type="button"
          onClick={() => void suggest()}
          disabled={busy || !assets.length}
          title="Cari kata kunci aset di dalam transkrip segmen"
        >
          Usulkan otomatis
        </button>
      </div>

      {error ? (
        <p className="alert" role="alert" style={{ fontSize: "0.75rem", marginTop: "0.5rem" }}>
          {error}
        </p>
      ) : null}
      {message ? (
        <p
          className="mono"
          role="status"
          style={{ fontSize: "0.7rem", marginTop: "0.5rem" }}
        >
          {message}
        </p>
      ) : null}

      {/* --- Timeline --- */}
      <div style={{ marginTop: "0.7rem" }}>
        <div
          style={{
            position: "relative",
            height: 46,
            border: "var(--bw) solid var(--line)",
            background: "var(--muted-bg)",
          }}
          aria-label="Timeline overlay"
        >
          {tickMarks.map((t) => (
            <div
              key={t}
              style={{
                position: "absolute",
                left: `${pct(t)}%`,
                top: 0,
                bottom: 0,
                borderLeft: "1px dashed rgba(0,0,0,0.18)",
              }}
            >
              <span className="mono" style={{ position: "absolute", bottom: -14, left: 1, fontSize: "0.55rem" }}>
                {t}s
              </span>
            </div>
          ))}

          {placements.map((p) => {
            const left = pct(p.start_s);
            const width = Math.max(1, pct(p.end_s) - left);
            const isSelected = selected?.id === p.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => setSelected(p)}
                title={`${p.asset_name} ${p.start_s.toFixed(1)}–${p.end_s.toFixed(1)}s`}
                style={{
                  position: "absolute",
                  left: `${left}%`,
                  width: `${width}%`,
                  top: 7,
                  height: 28,
                  background: KIND_COLOR[p.asset_kind] ?? "var(--accent)",
                  border: isSelected ? "3px solid var(--ink)" : "1px solid var(--ink)",
                  cursor: "pointer",
                  padding: 0,
                  overflow: "hidden",
                  // Usulan otomatis digambar bergaris putus supaya bedanya
                  // terlihat tanpa harus membuka detailnya.
                  borderStyle: p.suggested ? "dashed" : "solid",
                }}
                aria-label={`${p.asset_name} pada ${p.start_s.toFixed(1)} sampai ${p.end_s.toFixed(1)} detik`}
              >
                <span
                  className="mono"
                  style={{
                    fontSize: "0.55rem",
                    whiteSpace: "nowrap",
                    padding: "0 3px",
                    display: "block",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {p.asset_kind === "audio" ? "♪ " : ""}
                  {p.asset_name}
                </span>
              </button>
            );
          })}
        </div>
        <div className="mono" style={{ fontSize: "0.6rem", marginTop: "1rem", opacity: 0.7 }}>
          Total durasi segmen {durationS.toFixed(1)}s · {placements.length} overlay
        </div>
      </div>

      {/* --- Pengaturan overlay terpilih --- */}
      {selected && draft ? (
        <div
          className="panel"
          style={{ marginTop: "0.7rem", background: "var(--muted-bg)", padding: "0.7rem" }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="label" style={{ fontSize: "0.7rem" }}>
              {selected.asset_name}
              {selected.suggested ? " · usulan" : ""}
            </span>
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => void remove(selected)}
              style={{ fontSize: "0.62rem", padding: "0.15rem 0.45rem" }}
            >
              Hapus
            </button>
          </div>

          <div className="grid grid-2" style={{ gap: "0.6rem", marginTop: "0.5rem" }}>
            <div>
              <label className="label" htmlFor={fid("start")} style={{ fontSize: "0.62rem" }}>
                Mulai (s)
              </label>
              <input
                id={fid("start")}
                type="number"
                className="input"
                min={0}
                max={Math.max(0, durationS - 0.5)}
                step={0.1}
                value={startText}
                onChange={(e) => setStartText(e.target.value)}
                onBlur={(e) => commitTime("start_s", e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && commitTime("start_s", startText)}
                style={{ fontSize: "0.75rem" }}
              />
            </div>
            <div>
              <label className="label" htmlFor={fid("end")} style={{ fontSize: "0.62rem" }}>
                Selesai (s)
              </label>
              <input
                id={fid("end")}
                type="number"
                className="input"
                min={0.1}
                max={durationS}
                step={0.1}
                value={endText}
                onChange={(e) => setEndText(e.target.value)}
                onBlur={(e) => commitTime("end_s", e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && commitTime("end_s", endText)}
                style={{ fontSize: "0.75rem" }}
              />
            </div>
          </div>

          <div style={{ marginTop: "0.5rem" }}>
            <label className="label" htmlFor={fid("pos")} style={{ fontSize: "0.62rem" }}>
              Posisi
            </label>
            <select
              id={fid("pos")}
              className="input"
              value={selected.position}
              onChange={(e) =>
                void patchSelected({ position: e.target.value as OverlayPosition })
              }
              style={{ fontSize: "0.75rem" }}
            >
              {(options?.positions ?? []).map((pos) => (
                <option key={pos} value={pos}>
                  {pos.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-2" style={{ gap: "0.6rem", marginTop: "0.5rem" }}>
            <div>
              <label className="label" htmlFor={fid("scale")} style={{ fontSize: "0.62rem" }}>
                Ukuran ({(draft.scale * 100).toFixed(0)}% lebar)
              </label>
              <input
                id={fid("scale")}
                type="range"
                min={5}
                max={100}
                step={5}
                value={Math.round(draft.scale * 100)}
                onChange={(e) => setDraft({ ...draft, scale: Number(e.target.value) / 100 })}
                onPointerUp={() => void patchSelected({ scale: draft.scale })}
                onKeyUp={() => void patchSelected({ scale: draft.scale })}
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label className="label" htmlFor={fid("opacity")} style={{ fontSize: "0.62rem" }}>
                Opasitas ({draft.opacity}%)
              </label>
              <input
                id={fid("opacity")}
                type="range"
                min={0}
                max={100}
                step={5}
                value={draft.opacity}
                onChange={(e) => setDraft({ ...draft, opacity: Number(e.target.value) })}
                onPointerUp={() => void patchSelected({ opacity: draft.opacity })}
                onKeyUp={() => void patchSelected({ opacity: draft.opacity })}
                style={{ width: "100%" }}
              />
            </div>
          </div>

          <div style={{ marginTop: "0.5rem" }}>
            <label className="label" htmlFor={fid("trans")} style={{ fontSize: "0.62rem" }}>
              Transisi
            </label>
            <select
              id={fid("trans")}
              className="input"
              value={selected.transition}
              onChange={(e) =>
                void patchSelected({ transition: e.target.value as OverlayTransition })
              }
              style={{ fontSize: "0.75rem" }}
            >
              {(options?.transitions ?? []).map((tr) => (
                <option key={tr} value={tr}>
                  {tr}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : null}

      {/* --- Pustaka aset --- */}
      <div style={{ marginTop: "0.9rem" }}>
        <div className="label" style={{ fontSize: "0.68rem" }}>Pustaka aset</div>

        <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.4rem", flexWrap: "wrap" }}>
          <input
            className="input"
            value={uploadName}
            onChange={(e) => setUploadName(e.target.value)}
            placeholder="Nama aset…"
            style={{ flex: "1 1 8rem", fontSize: "0.72rem" }}
            aria-label="Nama aset overlay"
          />
          <input
            className="input"
            value={uploadTags}
            onChange={(e) => setUploadTags(e.target.value)}
            placeholder="Kata kunci: grafik,angka,persen"
            style={{ flex: "2 1 12rem", fontSize: "0.72rem" }}
            aria-label="Kata kunci pemicu"
          />
          <label className="btn" style={{ cursor: busy ? "wait" : "pointer", fontSize: "0.68rem" }}>
            {busy ? "…" : "Unggah"}
            <input
              type="file"
              accept="image/*,video/*,audio/*"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void upload(file);
                e.target.value = "";
              }}
              style={{ display: "none" }}
            />
          </label>
        </div>
        <p className="muted" style={{ fontSize: "0.62rem", marginTop: "0.3rem" }}>
          Kata kunci dipakai tombol &quot;Usulkan otomatis&quot; untuk menandai saat aset
          sebaiknya muncul.
        </p>

        {assets.map((asset) => (
          <div
            key={asset.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.4rem",
              padding: "0.25rem 0",
              borderBottom: "1px solid var(--muted-bg)",
            }}
          >
            <span
              className="badge"
              style={{ background: KIND_COLOR[asset.kind], fontSize: "0.58rem" }}
            >
              {asset.kind}
            </span>
            <span style={{ flex: 1, fontSize: "0.74rem" }}>{asset.name}</span>
            {asset.tags.length ? (
              <span className="mono muted" style={{ fontSize: "0.58rem" }}>
                {asset.tags.join(",")}
              </span>
            ) : null}
            <button
              className="btn btn-primary"
              type="button"
              disabled={busy}
              onClick={() => void place(asset)}
              style={{ fontSize: "0.6rem", padding: "0.12rem 0.4rem" }}
            >
              Tempatkan
            </button>
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => void removeAsset(asset)}
              style={{ fontSize: "0.6rem", padding: "0.12rem 0.4rem" }}
              aria-label={`Hapus aset ${asset.name}`}
            >
              Hapus
            </button>
          </div>
        ))}

        {!assets.length ? (
          <p className="muted" style={{ fontSize: "0.7rem", marginTop: "0.4rem" }}>
            Belum ada aset. Unggah gambar/video ilustrasi atau efek suara di atas.
          </p>
        ) : null}
      </div>
    </div>
  );
}
