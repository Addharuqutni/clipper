"""Komposisi overlay B-roll/efek suara ke dalam klip.

**Prinsip yang dipegang: satu lintasan FFmpeg, bukan render berantai.**
Merender ulang video untuk setiap overlay akan menurunkan kualitas gambar
setiap kali (setiap encode lossy) dan memakan waktu berlipat. Semua overlay
digabungkan ke dalam satu filtergraph: gambar/video sebagai lapisan
``overlay``, audio sebagai ``amix``.

**Bentuk filtergraph.** Untuk setiap overlay dihitung posisi pikselnya, lalu
disusun rantai::

    [0:v]  → (subtitle sudah ter-bakar) → [ov1] → [ov2] → ... → keluaran
    [1:v]  → scale/opacity/trim → ────────┘

Setiap sumber overlay diberi ``setpts`` agar hanya muncul pada rentang
waktunya, dan ``format=rgba`` agar transparansi PNG dihormati — tanpa
``format=rgba`` libavfilter membuang kanal alpha dan gambar akan tampil sebagai
kotak hitam pekat.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker_render.ffmpeg_pipe import ffmpeg_executable, run_ffmpeg

#: Posisi yang didukung, dipetakan ke ekspresi FFmpeg.
#:
#: Ekspresi memakai ``W``/``H`` (lebar/tinggi video utama) dan ``w``/``h``
#: (lebar/tinggi overlay) yang disediakan libavfilter, bukan angka tetap —
#: sehingga tetap benar untuk klip 9:16 maupun 16:9.
_POSITION_EXPR: dict[str, str] = {
    "top_left": "16:16",
    "top_right": "W-w-16:16",
    "bottom_left": "16:H-h-16",
    "bottom_right": "W-w-16:H-h-16",
    "center": "(W-w)/2:(H-h)/2",
    "top_center": "(W-w)/2:16",
    "bottom_center": "(W-w)/2:H-h-16",
    # "full" membentang menutupi seluruh frame; overlay-nya sendiri diskalakan
    # agar lebarnya = lebar video dan tinggi mengikuti rasio aslinya.
    "full": "0:0",
}


@dataclass(frozen=True, slots=True)
class OverlaySpec:
    """Satu overlay yang akan dikomposisikan."""

    path: Path
    #: Waktu MULAI/MULAI relatif terhadap awal klip.
    start_s: float
    end_s: float
    position: str = "top_right"
    #: Lebar overlay relatif terhadap lebar klip.
    scale: float = 0.35
    opacity: int = 100
    kind: str = "image"
    transition: str = "none"
    transition_ms: int = 300


def _fade_filters(spec: OverlaySpec) -> str:
    """Bangun filter fade masuk/keluar untuk sebuah overlay.

    Stream overlay sudah digeser ke waktu klip (``setpts=...+start/TB``), jadi
    fade memakai waktu klip absolut: masuk di ``start_s``, keluar menjelang
    ``end_s``. Menghitungnya dari 0 membuat overlay yang mulai setelah durasi
    fade sudah transparan penuh saat tampil.
    """
    if spec.transition != "fade":
        return ""

    duration = max(0.05, spec.end_s - spec.start_s)
    fade_s = min(spec.transition_ms / 1000.0, duration / 2)
    if fade_s <= 0:
        return ""

    # alpha=1: memudarkan kanal alpha, bukan kecerahannya. Tanpa ini, fade
    # terlihat sebagai "gambar menggelap" bukan "gambar muncul perlahan".
    fade_out = max(spec.start_s, spec.end_s - fade_s)
    return (
        f"fade=t=in:st={spec.start_s:.3f}:d={fade_s:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out:.3f}:d={fade_s:.3f}:alpha=1"
    )


def build_overlay_args(
    base_video: Path,
    output: Path,
    overlays: list[OverlaySpec],
    *,
    width: int,
    height: int,
    crf: int,
    preset: str,
    ffmpeg_threads: int,
) -> list[str]:
    """Susun argumen FFmpeg lengkap untuk mengomposisikan overlay.

    Args:
        base_video: video yang sudah di-crop dan (bila ada) sudah bersubtitle.
        output: tujuan berkas hasil.
        overlays: daftar overlay beserta posisi dan waktunya.
        width: lebar klip (dipakai menghitung skala overlay).
        height: tinggi klip.
        crf: kualitas encode.
        preset: preset x264.
        ffmpeg_threads: jumlah thread FFmpeg.

    Returns:
        Daftar argumen siap diberikan ke :func:`run_ffmpeg`.
    """
    args: list[str] = [
        ffmpeg_executable(),
        "-y", "-nostdin", "-loglevel", "error",
        # Video utama paling dulu (indeks 0) — filtergraph mengasumsikan itu.
        "-i", str(base_video),
    ]

    visual = [o for o in overlays if o.kind in {"image", "video"}]
    # Berkas audio dibuka sebagai input terpisah dan nanti di-mix.
    audio = [o for o in overlays if o.kind == "audio"]

    for spec in visual + audio:
        if spec.kind == "image":
            # `-loop 1` membuat gambar diam menjadi stream berdurasi. Tanpa ini,
            # gambar hanya satu frame dan tidak akan terlihat sama sekali di
            # dalam video.
            args += ["-loop", "1"]
            # Stream gambar sepanjang durasi tampilnya; stream lalu digeser ke
            # start_s lewat setpts di filtergraph.
            args += ["-t", f"{max(0.1, spec.end_s - spec.start_s):.3f}"]
        args += ["-i", str(spec.path)]

    filters: list[str] = []
    current = "0:v"

    for index, spec in enumerate(visual, start=1):
        target_width = max(2, int(width * spec.scale))
        opacity = max(0.0, min(1.0, spec.opacity / 100))
        position = _POSITION_EXPR.get(spec.position, _POSITION_EXPR["top_right"])

        # Rantai per-overlay:
        #   scale -> format rgba (jaga alpha) -> colorchannelmixer (opacity)
        #
        # **Kenapa TIDAK memakai setpts/trim untuk membatasi waktu.** Cara itu
        # salah dan sempat menjadi bug: `trim` memotong berdasarkan PTS stream
        # yang sudah digeser `setpts`, sehingga overlay kedua (mulai 4s) justru
        # terpotong habis sebelum muncul. Membatasi lewat `overlay:enable=`
        # jauh lebih sederhana dan benar — filter overlay menerima ekspresi
        # waktu berdasarkan PTS video UTAMA (0:v), yang memang yang kita mau.
        # setpts menggeser overlay ke waktu klip: video B-roll mulai dari
        # frame pertamanya tepat di start_s (bukan sudah berjalan sejak detik 0),
        # dan fade dapat memakai waktu klip absolut.
        chain = [
            f"setpts=PTS-STARTPTS+{spec.start_s:.3f}/TB",
            f"scale={target_width}:-2",
            "format=rgba",
        ]
        if opacity < 1.0:
            # aa = alpha dari input, dikalikan faktor opacity.
            chain.append(f"colorchannelmixer=aa={opacity:.3f}")
        fade = _fade_filters(spec)
        if fade:
            chain.append(fade)

        label = f"ov{index}"
        filters.append(f"[{index}:v]{','.join(chain)}[{label}s]")

        next_label = f"ov{index}o"
        # `enable` menerima ekspresi; `between(t,a,b)` aktif pada rentang itu.
        # `eof_action=pass` menjaga video utama terus berjalan setelah stream
        # overlay habis, dan `shortest=0` mencegah klip terpotong saat overlay
        # pertama selesai — dua bawaan libavfilter yang bila dibiarkan akan
        # memotong video di detik akhir overlay pertama.
        filters.append(
            f"[{current}][{label}s]overlay={position}:"
            f"enable='between(t,{spec.start_s:.3f},{spec.end_s:.3f})':"
            f"eof_action=pass:shortest=0[{next_label}]"
        )
        current = next_label

    audio_args: list[str] = []
    if audio:
        # Audio overlay dimix ke trek utama. `normalize=0` mencegah FFmpeg
        # menurunkan volume trek utama setiap kali ada efek suara — perilaku
        # bawaan amix yang membuat narasi jadi pelan tanpa alasan.
        mix_labels: list[str] = ["0:a"]
        for offset, spec in enumerate(audio):
            source_index = len(visual) + 1 + offset
            filters.append(
                # Ambil awal efek suara sepanjang durasi tampilnya, lalu tunda
                # ke start_s. atrim=start=S memotong bagian yang SALAH (detik S
                # dari berkas suara) dan memutarnya di detik 0.
                f"[{source_index}:a]atrim=0:{spec.end_s - spec.start_s:.3f},asetpts=PTS-STARTPTS,"
                f"adelay={int(spec.start_s * 1000)}:all=1,volume={spec.opacity / 100:.2f}[a{offset}]"
            )
            mix_labels.append(f"a{offset}")

        # Label digabung sebagai "[0:a][a0][a1]" — setiap label WAJIB memakai
        # tanda kurung siku, dan menggabungkannya dengan join() butuh kurung
        # di kedua sisi tiap elemen.
        joined = "".join(f"[{label}]" for label in mix_labels)
        filters.append(
            f"{joined}amix=inputs={len(mix_labels)}:duration=first:normalize=0[aout]"
        )
        audio_args = ["-map", f"[{current}]" if visual else "0:v", "-map", "[aout]"]

    args += ["-filter_complex", ";".join(filters)]
    if not audio_args:
        # Tanpa audio overlay: salin trek suara apa adanya (tidak di-encode ulang).
        args += ["-map", f"[{current}]" if visual else "0:v", "-map", "0:a?", "-c:a", "copy"]
    else:
        args += audio_args + ["-c:a", "aac", "-b:a", "192k"]

    args += [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-threads", str(ffmpeg_threads),
        str(output),
    ]
    return args


def composite_overlays(
    base_video: Path,
    output: Path,
    overlays: list[OverlaySpec],
    *,
    width: int,
    height: int,
    crf: int,
    preset: str,
    ffmpeg_threads: int,
) -> dict[str, Any]:
    """Komposisikan overlay ke dalam video.

    Bila tidak ada overlay, video disalin apa adanya (tanpa encode ulang) —
    encode ulang tanpa perubahan hanya menurunkan kualitas.

    Raises:
        RuntimeError: bila FFmpeg gagal, dengan potongan stderr untuk diagnosis.
    """
    if not overlays:
        import shutil

        shutil.copyfile(base_video, output)
        return {"composited": False, "count": 0}

    args = build_overlay_args(
        base_video,
        output,
        overlays,
        width=width,
        height=height,
        crf=crf,
        preset=preset,
        ffmpeg_threads=ffmpeg_threads,
    )
    result = run_ffmpeg(args)

    if not result.ok:
        raise RuntimeError(
            f"Gagal mengomposisikan {len(overlays)} overlay: {result.stderr_tail}"
        )

    return {
        "composited": True,
        "count": len(overlays),
        "visual": len([o for o in overlays if o.kind in {"image", "video"}]),
        "audio": len([o for o in overlays if o.kind == "audio"]),
    }
