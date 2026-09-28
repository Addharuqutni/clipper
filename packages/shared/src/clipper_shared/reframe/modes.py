"""Mode reframing video dan perhitungan crop-nya.

Tiga mode ditawarkan (permintaan pengguna, mengacu repo `jipraks/yt-short-clipper`):

* ``FACE_TRACK``   — crop mengikuti wajah pembicara secara dinamis.
* ``BLACK_BARS``   — video utuh diperkecil, sisanya diisi bar hitam.
* ``BLURRED_FILL`` — video utuh diperkecil, latar diisi versi blur dari video.

Modul ini SENGAJA hanya memuat matematika dan pemilihan filter FFmpeg —
tanpa impor MediaPipe/OpenCV. Dengan begitu ia bisa diuji di container API
yang tidak punya dependensi berat, dan worker-render yang memegang OpenCV
hanya perlu memanggil fungsi di sini.

Pemetaan mode ke filter FFmpeg:

* ``BLACK_BARS``   → ``pad``  (tanpa biaya komputasi tambahan)
* ``BLURRED_FILL`` → ``split`` + ``scale`` + ``crop`` + ``boxblur`` + ``overlay``
                     (paling mahal: video diproses dua kali)
* ``FACE_TRACK``   → ``crop`` dengan posisi x berubah per frame, dihitung
                     terpisah oleh :mod:`clipper_shared.reframe.tracker`
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CropMode(StrEnum):
    """Mode penyusunan klip vertikal."""

    FACE_TRACK = "face_track"
    BLACK_BARS = "black_bars"
    BLURRED_FILL = "blurred_fill"


#: Nilai bawaan = mode paling umum diminta pengguna untuk podcast/wawancara.
DEFAULT_CROP_MODE = CropMode.FACE_TRACK

#: Resolusi keluaran wajib (PRD FR-4.3: Full HD vertikal).
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

#: Kekuatan blur latar untuk ``blurred_fill``. Cukup kuat agar tidak ada detail
#: yang mencuri perhatian dari subjek utama, tetapi tetap memberi konteks warna.
BLUR_SIGMA = 22


@dataclass(frozen=True, slots=True)
class CropGeometry:
    """Perhitungan ukuran untuk menempatkan video sumber di kanvas vertikal."""

    source_width: int
    source_height: int
    output_width: int
    output_height: int
    #: Ukuran video setelah diskalakan agar muat di kanvas.
    scaled_width: int
    scaled_height: int
    #: Geseran penempatan (untuk mode berbar).
    offset_x: int
    offset_y: int

    @property
    def needs_bar_fill(self) -> bool:
        """True bila video tidak mengisi penuh kanvas sehingga perlu latar."""
        return self.scaled_width < self.output_width or self.scaled_height < self.output_height


def plan_letterbox(
    source_width: int,
    source_height: int,
    output_width: int = OUTPUT_WIDTH,
    output_height: int = OUTPUT_HEIGHT,
) -> CropGeometry:
    """Hitung penempatan "muat seluruh video" di dalam kanvas vertikal.

    Video diskalakan agar **seluruh** bingkainya terlihat (``decrease``), lalu
    dipusatkan. Pada sumber 16:9, hasilnya adalah video lebar di tengah dengan
    ruang kosong di atas dan bawah — ruang itu diisi bar hitam atau blur
    tergantung mode.

    Semua nilai dibulatkan ke bilangan **genap**: encoder H.264 dengan
    ``yuv420p`` mensyaratkan dimensi genap, dan melanggar ini menghasilkan error
    ffmpeg yang membingungkan pengguna.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValueError("dimensi sumber harus positif")

    width_ratio = output_width / source_width
    height_ratio = output_height / source_height
    scale = min(width_ratio, height_ratio)

    # Pembulatan ke KELIPATAN 4, bukan sekadar genap.
    #
    # Alasannya aritmetika: kanvas 1920 dan tinggi skala 606 menyisakan 1314
    # piksel. Dua bar genap yang sama tinggi masing-masing 657 — bukan bilangan
    # genap, jadi tidak mungkin. Akibatnya bar menjadi 656 dan 658: selisih 2
    # piksel yang terlihat sebagai video agak meleset dari tengah.
    #
    # Dengan dimensi kelipatan 4, sisanya selalu habis dibagi 4 sehingga kedua
    # bar bisa sama tinggi DAN tetap genap. Ini juga selaras dengan subsampling
    # kroma H.264 yang idealnya berkelipatan 2 (dan lebih aman pada 4).
    scaled_width = _to_multiple_of_4(max(4, int(source_width * scale)))
    scaled_height = _to_multiple_of_4(max(4, int(source_height * scale)))

    # Pastikan tidak melampaui kanvas setelah pembulatan.
    scaled_width = min(scaled_width, _to_multiple_of_4(output_width))
    scaled_height = min(scaled_height, _to_multiple_of_4(output_height))

    offset_x = _balance_offset(output_width - scaled_width)
    offset_y = _balance_offset(output_height - scaled_height)

    return CropGeometry(
        source_width=source_width,
        source_height=source_height,
        output_width=output_width,
        output_height=output_height,
        scaled_width=scaled_width,
        scaled_height=scaled_height,
        offset_x=offset_x,
        offset_y=offset_y,
    )


def _balance_offset(slack: int) -> int:
    """Bagi sisa ruang serata mungkin sambil menjaga nilai genap.

    ``slack`` adalah ruang kosong total. Hasilnya kelipatan 2 supaya bar atas
    dan bawah (atau kiri dan kanan) seimbang.
    """
    if slack <= 0:
        return 0
    return _to_even(slack // 2)


def _to_multiple_of_4(value: int) -> int:
    """Bulatkan ke bawah ke kelipatan 4.

    Lebih ketat daripada sekadar genap: kelipatan 4 membuat sisa ruang selalu
    habis dibagi 4, sehingga bar kiri/kanan dan atas/bawah bisa benar-benar
    sama besar, bukan berselisih 2 piksel.
    """
    return value - (value % 4)


def _to_even(value: int) -> int:
    """Bulatkan ke bawah ke bilangan genap."""
    return value - (value % 2)


def crop_width_for(source_width: int, source_height: int, output_width: int = OUTPUT_WIDTH, output_height: int = OUTPUT_HEIGHT) -> int:
    """Lebar crop untuk mode pelacakan wajah.

    Crop memakai **tinggi penuh** sumber lalu memotong lebarnya sesuai rasio
    9:16. Bila sumber lebih sempit dari 9:16, seluruh lebar dipakai — memakai
    rumus tanpa penjagaan akan menghasilkan lebar negatif.
    """
    if source_height <= 0:
        raise ValueError("tinggi sumber harus positif")
    desired = int(source_height * output_width / output_height)
    return _to_even(max(2, min(desired, source_width)))


def build_filter_chain(
    mode: CropMode,
    geometry: CropGeometry,
    crop_x_expression: str | None = None,
) -> str:
    """Susun rantai filter FFmpeg untuk mode yang dipilih.

    ``crop_x_expression`` hanya dipakai oleh ``FACE_TRACK``: ekspresi FFmpeg
    yang menghasilkan posisi x per frame (mis. dari berkas posisi yang dikirim
    melalui ``sendcmd``). Mode lain tidak membutuhkannya karena posisinya tetap.
    """
    out = f"{geometry.output_width}:{geometry.output_height}"

    if mode is CropMode.BLACK_BARS:
        # `pad` lebih murah daripada scale+overlay: hanya menambah piksel,
        # tanpa memproses video dua kali.
        return (
            f"scale={geometry.scaled_width}:{geometry.scaled_height}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={out}:{geometry.offset_x}:{geometry.offset_y}:color=black"
        )

    if mode is CropMode.BLURRED_FILL:
        # Latar: skala paksa ke kanvas penuh (merusak rasio, disengaja) lalu
        # diblur. Bentuk `boxblur` dipilih karena jauh lebih cepat daripada
        # `gblur` pada CPU tanpa GPU, dengan hasil yang sama memadai.
        return (
            f"split=2[bg][fg];"
            f"[bg]scale={out}:force_original_aspect_ratio=increase,"
            f"crop={out},boxblur={BLUR_SIGMA}:2[bgblur];"
            f"[fg]scale={geometry.scaled_width}:{geometry.scaled_height}:"
            f"force_original_aspect_ratio=decrease[fgscaled];"
            f"[bgblur][fgscaled]overlay={geometry.offset_x}:{geometry.offset_y}"
        )

    # FACE_TRACK: crop berukuran tetap, posisi x bergerak.
    crop_w = crop_width_for(geometry.source_width, geometry.source_height)
    x_expr = crop_x_expression or f"(in_w-{crop_w})/2"
    return f"crop={crop_w}:{geometry.source_height}:{x_expr}:0,scale={out}"


def describe_mode(mode: CropMode) -> str:
    """Penjelasan singkat untuk UI. Bahasa Indonesia, bukan istilah teknis."""
    match mode:
        case CropMode.FACE_TRACK:
            return "Kamera mengikuti wajah pembicara secara otomatis."
        case CropMode.BLACK_BARS:
            return "Seluruh video terlihat, dengan bar hitam di atas dan bawah."
        case CropMode.BLURRED_FILL:
            return "Seluruh video terlihat, latar diisi versi blur dari video itu sendiri."
