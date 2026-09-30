"use client";

// Pratinjau gaya caption di browser.
//
// **Mengapa pratinjau di DOM, bukan render FFmpeg.** Render membutuhkan antrean
// worker (detik sampai menit) dan hanya tersedia pada mesin yang punya FFmpeg +
// MediaPipe. Menyetel warna lalu menunggu satu menit untuk melihat hasilnya
// membuat penyetelan gaya menjadi pekerjaan yang menyiksa. Pratinjau DOM
// memberi umpan balik seketika untuk hal-hal yang paling sering disetel:
// ukuran, warna, posisi, outline, dan gerakan.
//
// **Batasnya diakui secara terbuka.** Ini bukan tiruan pixel-perfect libass:
// pembungkusan baris dan metrik font berbeda. Teks peringatan di bawah pratinjau
// menyatakan itu, supaya pengguna tidak mengira hasil render pasti identik.
import { useEffect, useMemo, useRef, useState } from "react";
import type { CaptionStyle } from "@/lib/types";

interface Props {
  style: CaptionStyle;
  /** Teks contoh yang ditampilkan. */
  sample?: string;
}

/** Kanvas acuan yang sama dengan backend (lihat SubtitleStyle). */
const REF_WIDTH = 1080;
const REF_HEIGHT = 1920;

export default function CaptionPreview({ style, sample }: Props) {
  const text = sample ?? "Ini contoh caption keren";
  const words = useMemo(() => text.split(/\s+/).filter(Boolean), [text]);
  const [activeWord, setActiveWord] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const [boxWidth, setBoxWidth] = useState(220);

  // Ukur lebar pratinjau agar metrik dari kanvas acuan 1080 bisa diskalakan
  // dengan benar. Tanpa ini, teks 65px yang benar untuk klip 1080px akan tampak
  // raksasa di kotak pratinjau selebar 200px.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => setBoxWidth(el.clientWidth || 220);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Putar kata aktif untuk menunjukkan animasi/pewarnaan sorot.
  useEffect(() => {
    setActiveWord(0);
    if (style.animation === "none") return;
    const timer = window.setInterval(() => {
      setActiveWord((i) => (i + 1) % words.length);
    }, 900);
    return () => window.clearInterval(timer);
  }, [words.length, style.animation, style.chunk_size]);

  const scale = boxWidth / REF_WIDTH;
  const fontSize = Math.max(8, style.font_size * scale);
  const outlineWidth = Math.max(0, style.outline * scale);
  const shadowOffset = style.shadow * scale;
  // margin_vertical diukur dari bawah; kotak pratinjau tingginya 16/9 * lebar.
  const boxHeight = boxWidth * (REF_HEIGHT / REF_WIDTH);
  const bottom = style.margin_vertical * scale;

  // Kelompokkan kata sesuai chunk_size agar yang tampil cocok dengan jumlah
  // kata per cue yang akan dirender.
  const chunkStart = Math.floor(activeWord / style.chunk_size) * style.chunk_size;
  const visible = words.slice(chunkStart, chunkStart + style.chunk_size);

  const animationClass =
    style.animation === "fade"
      ? "caption-anim-fade"
      : style.animation === "bounce" || style.animation === "pop"
        ? "caption-anim-pop"
        : "";

  const hasBox = style.border_style === 3;

  return (
    <div>
      <div
        ref={containerRef}
        style={{
          position: "relative",
          width: "100%",
          // Dibatasi lewat lebar (bukan maxHeight) agar rasio 9:16 tetap utuh:
          // scale, bottom, dan margin_side dihitung dari clientWidth. Batas vh
          // menjaga kotak + panel di bawahnya muat di layar laptop pendek.
          maxWidth: "min(100%, 220px, calc((100vh - 16rem) * 9 / 16))",
          margin: "0 auto",
          // 9:16 — sama dengan klip keluaran.
          aspectRatio: "9 / 16",
          background: "linear-gradient(160deg, #2b2b2b, #101010)",
          border: "var(--bw) solid var(--line)",
          overflow: "hidden",
        }}
        aria-label="Pratinjau gaya caption"
      >
        {/* Siluet pembicara, hanya penanda posisi — bukan bagian dari gaya. */}
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: "12%",
            left: "50%",
            transform: "translateX(-50%)",
            width: "42%",
            aspectRatio: "1",
            borderRadius: "50%",
            background: "rgba(255,255,255,0.07)",
          }}
        />

        <div
          style={{
            position: "absolute",
            left: style.margin_side * scale,
            right: style.margin_side * scale,
            bottom,
            textAlign:
              style.alignment % 3 === 1
                ? "left"
                : style.alignment % 3 === 3
                  ? "right"
                  : "center",
            fontFamily: `"${style.font_name}", "Arial Black", sans-serif`,
            fontSize,
            fontWeight: style.bold ? 900 : 700,
            fontStyle: style.italic ? "italic" : "normal",
            letterSpacing: style.letter_spacing * scale,
            lineHeight: 1.15,
            // Bayangan dan outline: outline dibuat dengan text-shadow berlapis
            // karena CSS tidak punya properti outline teks.
            color: style.primary_rgb,
            textShadow: buildTextShadow(style, outlineWidth, shadowOffset),
            // Warna kotak latar dihitung di sini (bukan lewat helper terpisah):
            // nilainya hanya dipakai sekali dan rumusnya sudah jelas dari barisnya.
            background: (() => {
              if (!hasBox) return "transparent";
              const hex = style.back_rgb.replace("#", "");
              if (hex.length !== 6) return "transparent";
              const r = parseInt(hex.slice(0, 2), 16);
              const g = parseInt(hex.slice(2, 4), 16);
              const b = parseInt(hex.slice(4, 6), 16);
              const alpha = Math.min(100, Math.max(0, style.back_alpha)) / 100;
              return `rgba(${r}, ${g}, ${b}, ${alpha})`;
            })(),
            padding: hasBox ? `${fontSize * 0.18}px ${fontSize * 0.35}px` : 0,
            display: hasBox ? "inline-block" : "block",
            maxWidth: "100%",
            boxDecorationBreak: "clone",
          }}
          key={`${chunkStart}-${style.animation}`}
          className={animationClass}
        >
          {visible.map((word, i) => {
            const isActive = chunkStart + i === activeWord;
            return (
              <span
                key={`${word}-${i}`}
                style={{ color: isActive ? style.highlight_rgb : style.primary_rgb }}
              >
                {word}
                {i < visible.length - 1 ? " " : ""}
              </span>
            );
          })}
        </div>

        {/* Batas area aman platform, membantu memilih margin_vertical. */}
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: "12%",
            borderTop: "1px dashed rgba(255,255,255,0.18)",
          }}
        />
      </div>

      <p className="hint" style={{ marginTop: "0.4rem" }}>
        Pratinjau perkiraan. Pembungkusan baris dan metrik font bisa berbeda
        sedikit dari hasil render FFmpeg.
      </p>
      <div className="mono" style={{ fontSize: "0.75rem", opacity: 0.7 }}>
        {Math.round(boxWidth)}px lebar pratinjau · {style.animation}
        {animationClass ? " (beranimasi)" : ""}
      </div>
    </div>
  );
}

/** Susun bayangan teks: outline berlapis + drop shadow opsional. */
function buildTextShadow(style: CaptionStyle, outlineWidth: number, shadowOffset: number): string {
  const parts: string[] = [];
  if (outlineWidth > 0) {
    // Delapan arah mengelilingi huruf menghasilkan tepi yang rata. Empat arah
    // meninggalkan sudut yang terlihat "bergerigi" pada font tebal.
    const offsets: Array<[number, number]> = [
      [-1, -1], [0, -1], [1, -1],
      [-1, 0], [1, 0],
      [-1, 1], [0, 1], [1, 1],
    ];
    for (const [dx, dy] of offsets) {
      parts.push(`${dx * outlineWidth}px ${dy * outlineWidth}px 0 ${style.outline_rgb}`);
    }
  }
  if (shadowOffset > 0) {
    parts.push(`${shadowOffset}px ${shadowOffset}px ${shadowOffset * 0.6}px rgba(0,0,0,0.75)`);
  }
  parts.push("0 0 2px rgba(0,0,0,0.35)");
  return parts.join(", ");
}
