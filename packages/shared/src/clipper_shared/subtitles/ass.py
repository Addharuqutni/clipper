"""Generator subtitle ASS dengan efek karaoke per kata.

Pola diambil dari repo referensi `jipraks/yt-short-clipper`
(`caption_generator.py`), diadaptasi ke arsitektur ClipperAI. Lihat
TECH_SPEC §5.2.1 dan docs/memory/references.md.

Pendekatan: SATU event ``Dialogue`` per kata, dengan potongan beberapa kata
diulang dan hanya kata berjalan yang diwarnai. Ini menghasilkan efek karaoke
tanpa memakai tag ``\\k`` — tag itu perlu durasi per suku kata yang tidak
tersedia dari Whisper.

DUA JEBAKAN yang dihindari modul ini (keduanya senyap, bukan error):

1. **``PlayResX``/``PlayResY`` wajib dari resolusi nyata.** libass meregangkan
   kanvas yang dideklarasikan agar memenuhi frame. Kalau ditulis tetap
   1080x1920, subtitle pada klip non-9:16 akan gepeng (melebar 1,78x,
   memendek 0,56x).
2. **Warna ASS adalah ``&HAABBGGRR`` — urutan BGR, bukan RGB.** Kuning
   ``#FFFF00`` menjadi ``&H00FFFF``. Salah urutan menukar merah dan biru
   tanpa peringatan apa pun.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Kanvas acuan tempat semua metrik gaya ditulis. Nilai gaya diskalakan dari
#: sini ke resolusi nyata klip.
REF_WIDTH = 1080
REF_HEIGHT = 1920

#: Jumlah kata yang ditampilkan sekaligus dalam satu potongan.
DEFAULT_CHUNK_SIZE = 4

#: Jenis animasi teks yang didukung. Ditulis sebagai konstanta (bukan literal
#: di dalam fungsi) supaya endpoint API dan validator memakai daftar yang sama —
#: nilai yang lolos validasi API tidak boleh ditolak di sini.
ANIMATION_KINDS: frozenset[str] = frozenset({"none", "fade", "bounce", "pop", "karaoke"})


def _ass_color(rgb_hex: str) -> str:
    """Ubah ``#RRGGBB`` menjadi ``&H00BBGGRR&`` yang dimengerti libass.

    libass memakai urutan byte BGR, bukan RGB. Mengabaikan ini menukar merah
    dan biru pada subtitle tanpa error — sulit dilacak secara visual karena
    teks tetap terbaca.
    """
    value = rgb_hex.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"warna harus berbentuk #RRGGBB, bukan {rgb_hex!r}")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}&".upper()


def format_ass_time(seconds: float) -> str:
    """Ubah detik menjadi ``H:MM:SS.cc``.

    ASS memakai **centisecond** (dua digit), bukan milidetik. Menulis tiga
    digit membuat waktu dibaca salah oleh libass.
    """
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    # Pembulatan bisa menghasilkan 100; pindahkan ke detik.
    if centis >= 100:
        centis -= 100
        secs += 1
        if secs >= 60:
            secs -= 60
            minutes += 1
            if minutes >= 60:
                minutes -= 60
                hours += 1
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


@dataclass(frozen=True, slots=True)
class SubtitleStyle:
    """Gaya visual subtitle. Semua nilai ditulis untuk kanvas acuan 1080x1920.

    Angka bawaan berasal dari repo referensi (lihat docs/memory/references.md) dan
    sudah terbukti terbaca pada klip vertikal.
    """

    font_name: str = "Arial Black"
    font_size: int = 65
    primary_rgb: str = "#FFFFFF"
    highlight_rgb: str = "#FFFF00"
    outline_rgb: str = "#000000"
    outline: int = 4
    shadow: int = 2
    margin_side: int = 50
    #: 400 dari acuan: menempatkan teks sekitar 1/5 tinggi frame dari bawah,
    #: di atas area kontrol platform dan di bawah wajah pembicara.
    margin_vertical: int = 400
    #: 1 = outline + drop shadow, tanpa kotak latar.
    border_style: int = 1
    #: 2 = bawah-tengah.
    alignment: int = 2

    # --- Warna tambahan (Fitur Kustomisasi Teks) ---
    #: Warna kotak latar; hanya terlihat bila ``border_style`` = 3 (opaque box).
    back_rgb: str = "#000000"
    #: Transparansi kotak latar, 0 (tembus) – 100 (pekat). Dipakai bersama
    #: ``border_style`` = 3 untuk gaya "caption box" yang umum di feed vertikal.
    back_alpha: int = 40
    #: True = teks dicetak miring.
    italic: bool = False
    #: True = teks dicetak tebal. Perlu diperhatikan: ``Arial Black`` sudah
    #: sangat tebal, jadi menyalakan ini bersama font tersebut membuat huruf
    #: saling menempel dan sulit dibaca — dibiarkan sebagai pilihan eksplisit.
    bold: bool = False
    #: Jarak antarhuruf, dalam satuan kanvas acuan. Nilai kecil (0–3) membantu
    #: teks bercetak tebal tetap terbaca; nilai besar memberi kesan "spaced".
    letter_spacing: float = 0

    # --- Animasi (Fitur Kustomisasi Teks) ---
    #: ``none``   — kemunculan biasa (bawaan; paling aman untuk semua encoder).
    #: ``fade``   — memudar masuk pada tiap cue, gaya "clean/subtitle".
    #: ``bounce`` — membesar lalu mengendur; gaya MrBeast/Hormozi yang menahan
    #:              perhatian tanpa mengubah tata letak.
    #: ``pop``    — mulai kecil dan membesar cepat; terasa lebih tajam daripada
    #:              bounce, cocok untuk penekanan kata kunci.
    #: ``karaoke``— hanya mewarnai kata aktif (perilaku lama) tanpa gerakan.
    animation: str = "karaoke"
    #: Berapa milidetik durasi animasi masuk. 0 berarti memakai bawaan animasi.
    animation_ms: int = 220
    #: Skala awal animasi (1.0 = tanpa perubahan ukuran). Nilai > 1 berarti
    #: teks muncul lebih besar lalu menyusut (bounce/pop mengendur).
    animation_scale: float = 1.25

    #: Ukuran potongan kata per cue (berapa kata tampil sekaligus).
    #: 1 = satu kata per layar (gaya "talking head" cepat), 4 = bawaan seimbang.
    chunk_size: int = 4

    def __post_init__(self) -> None:
        """Tolak gaya yang pasti menghasilkan ASS rusak, sedini mungkin.

        **Mengapa validasi di sini, bukan di endpoint.** Nilai gaya datang dari
        JSONB milik pengguna dan juga dari preset tersimpan. Kalau nilai buruk
        baru ketahuan saat FFmpeg berjalan, kegagalannya muncul sebagai render
        yang gagal setelah menit-menitan menunggu — bukan pesan yang bisa
        ditindaklanjuti. Memvalidasi di konstruktor membuat objek yang tidak
        sah mustahil dibuat.
        """
        if self.alignment not in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
            raise ValueError(f"alignment harus 1–9, bukan {self.alignment}")
        if self.border_style not in {1, 3}:
            raise ValueError("border_style harus 1 (outline) atau 3 (opaque box)")
        if not 0 <= self.back_alpha <= 100:
            raise ValueError("back_alpha harus 0–100")
        if self.animation not in ANIMATION_KINDS:
            raise ValueError(
                f"animation '{self.animation}' tidak dikenal; pilih salah satu {sorted(ANIMATION_KINDS)}"
            )
        if self.chunk_size < 1:
            raise ValueError("chunk_size minimal 1")
        for name, value in (
            ("font_size", self.font_size),
            ("outline", self.outline),
            ("shadow", self.shadow),
            ("margin_side", self.margin_side),
            ("margin_vertical", self.margin_vertical),
        ):
            if value < 0:
                raise ValueError(f"{name} tidak boleh negatif, bukan {value}")
        if self.animation_scale < 1.0:
            raise ValueError(
                "animation_scale minimal 1.0 — nilai di bawah 1 membuat teks "
                "mulai dari ukuran tak terlihat, yang terbaca sebagai tidak muncul"
            )
        # Warna divalidasi sekarang juga supaya kesalahan muncul di sini.
        for rgb in (self.primary_rgb, self.highlight_rgb, self.outline_rgb, self.back_rgb):
            _ass_color(rgb)


@dataclass(frozen=True, slots=True)
class SubtitleWord:
    """Satu kata beserta waktunya."""

    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """Satu event ``Dialogue``: potongan kata dengan satu kata disorot."""

    text: str
    start_s: float
    end_s: float


def _scaled(value: float, factor: float, minimum: int = 0) -> int:
    """Skalakan metrik gaya dari kanvas acuan, jaga agar tidak di bawah minimum."""
    return max(minimum, round(value * factor))


def _with_alpha(ass_color: str, alpha_percent: int) -> str:
    """Terapkan alpha transparansi ke warna ASS ``&HAABBGGRR&``.

    ASS memakai **alpha terbalik**: ``00`` berarti pekat dan ``FF`` berarti
    tembus pandang. Itu kebalikan dari intuisi dan penyebab umum "kotak latar
    hilang padahal alpha disetel 100" — jadi konversinya dikerjakan di satu
    tempat, dengan nama yang menjelaskannya.

    Args:
        ass_color: warna berbentuk ``&H00BBGGRR&`` dari :func:`_ass_color`.
        alpha_percent: 0 = tembus, 100 = pekat (skala yang intuitif bagi pengguna).
    """
    value = alpha_percent
    # 100% pekat -> alpha ASS 00; 0% -> FF.
    alpha_byte = round((100 - value) * 255 / 100)
    # ass_color dari _ass_color() berbentuk "&H00" + "BBGGRR" + "&".
    # Ambil 6 digit RGB-nya saja (indeks 4..-1), lalu tambahkan alpha di depan —
    # hasilnya "&HAABBGGRR&" yang benar. Menyisipkan alpha tanpa membuang "00"
    # awal menghasilkan 10 digit, dan libass membaca itu sebagai warna rusak.
    rgb = ass_color[4:-1]
    return f"&H{alpha_byte:02X}{rgb}&"


def _animation_tags(style: SubtitleStyle, duration_s: float) -> str:
    """Bangun tag override ASS untuk animasi masuk sebuah cue.

    Setiap animasi ditulis sebagai ``\\t(...)`` (transformasi berjalan) yang
    mengubah skala/alpha dari nilai awal ke nilai akhir. Semua animasi
    **berakhir pada alpha penuh dan skala 1.0**, sehingga teks selalu berakhir
    dalam keadaan yang sama seperti gaya tanpa animasi — kalau tidak, teks
    yang "berhenti di tengah animasi" akan tampak buram atau terlalu besar.

    Args:
        style: gaya aktif, sumber jenis animasi dan durasinya.
        duration_s: durasi cue; animasi dipendekkan agar tidak melebihi cue
            (pada kata yang sangat cepat, animasi 220 ms pada cue 80 ms akan
            membuat dua kata beranimasi bersamaan).
    """
    if style.animation in {"none", "karaoke"}:
        return ""

    # Durasi efektif: jangan lebih panjang dari 60% cue, supaya teks sempat
    # diam dalam keadaan normal sebelum kata berikutnya menggantikannya.
    max_ms = int(max(40.0, duration_s * 1000 * 0.6))
    ms = min(style.animation_ms, max_ms)

    if style.animation == "fade":
        # Mulai tembus (alpha FF = tembus) lalu menuju pekat.
        return f"{{\\alpha&HFF&\\t(0,{ms},\\alpha&H00&)}}"

    if style.animation in {"bounce", "pop"}:
        scale_from = int(style.animation_scale * 100)
        # ``pop`` mulai tanpa alpha (muncul dari ketiadaan) dan tumbuh cepat.
        # ``bounce`` mulai terlihat lalu menyusut ke ukuran normal, yang terasa
        # seperti memantul karena mata membaca perubahan ukuran sebagai gerak.
        start_alpha = "\\alpha&HFF&" if style.animation == "pop" else ""
        return (
            f"{{\\fscx{scale_from}\\fscy{scale_from}{start_alpha}"
            f"\\t(0,{ms},\\fscx100\\fscy100\\alpha&H00&)}}"
        )

    return ""


def _style_line(style: SubtitleStyle, width: int, height: int) -> str:
    """Susun baris ``Style:`` dengan metrik yang sudah diskalakan ke frame nyata.

    Ukuran font, outline, shadow, dan margin vertikal mengikuti **tinggi**;
    margin samping mengikuti **lebar**. Pada klip 9:16 rasio keduanya 1.0,
    sehingga gaya aslinya tidak berubah.

    Format kolom ASS (v4.00+) yang diisi di sini::

        Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,
        BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,
        BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
    """
    scale_x = width / REF_WIDTH
    scale_y = height / REF_HEIGHT

    font_size = _scaled(style.font_size, scale_y, minimum=8)
    outline = _scaled(style.outline, scale_y, minimum=1)
    shadow = _scaled(style.shadow, scale_y, minimum=0)
    margin_side = _scaled(style.margin_side, scale_x, minimum=0)
    margin_vertical = _scaled(style.margin_vertical, scale_y, minimum=0)

    primary = _ass_color(style.primary_rgb)
    outline_colour = _ass_color(style.outline_rgb)
    # Ketebalan kotak latar memakai alpha, bukan warna: libass membaca alpha
    # dari byte tertinggi, dan menaruh alpha di warna akan menimpa
    # transparansi yang sudah disetel pengguna.
    back_colour = _with_alpha(_ass_color(style.back_rgb), style.back_alpha)

    # Bold/Italic ditulis -1 (aktif) atau 0, BUKAN 1/0. ASS memakai -1 sebagai
    # "true" untuk dua kolom ini; menulis 1 membuat sebagian parser mengabaikannya.
    bold = -1 if style.bold else 0
    italic = -1 if style.italic else 0

    return (
        f"Style: Default,{style.font_name},{font_size},{primary},{primary},"
        f"{outline_colour},{back_colour},{bold},{italic},0,0,100,100,"
        f"{style.letter_spacing:g},0,"
        f"{style.border_style},{outline},{shadow},{style.alignment},"
        f"{margin_side},{margin_side},{margin_vertical},1"
    )


def build_cues(
    words: Sequence[SubtitleWord],
    chunk_size: int | None = None,
    style: SubtitleStyle | None = None,
) -> list[SubtitleCue]:
    """Kelompokkan kata menjadi cue karaoke.

    Setiap cue memuat satu potongan kata; kata yang sedang diucapkan disorot
    dengan ``style.highlight_rgb``, sisanya memakai ``style.primary_rgb``.
    Sebuah cue berlangsung selama kata yang disorot, lalu cue berikutnya mulai.
    Cue terakhir dari setiap potongan membentang sampai kata berikutnya, sehingga
    tidak ada celah waktu yang membuat subtitle berkedip hilang.

    Args:
        words: kata bertimestamp yang sudah dibersihkan.
        chunk_size: berapa kata per cue. ``None`` (bawaan) berarti ikuti
            ``style.chunk_size``; nilai eksplisit menimpanya untuk kasus uji.
        style: gaya aktif; menentukan warna sorot dan jenis animasi.
    """
    if chunk_size is not None and chunk_size < 1:
        raise ValueError("chunk_size minimal 1")

    clean = [w for w in words if w.text.strip() and w.end_s > w.start_s]
    if not clean:
        return []

    active_style = style or SubtitleStyle()
    effective_chunk = chunk_size if chunk_size is not None else active_style.chunk_size
    if effective_chunk < 1:
        raise ValueError("chunk_size minimal 1")
    # Dihitung sekali: `_ass_color` melempar ValueError untuk warna cacat, dan
    # kita ingin kegagalan itu terjadi di sini, bukan di tengah render.
    highlight = _ass_color(active_style.highlight_rgb)
    primary = _ass_color(active_style.primary_rgb)

    cues: list[SubtitleCue] = []
    for block_start in range(0, len(clean), effective_chunk):
        block = clean[block_start : block_start + effective_chunk]
        for offset, active in enumerate(block):
            parts: list[str] = []
            for index, word in enumerate(block):
                escaped = word.text.strip().replace("{", "(").replace("}", ")")
                if index == offset:
                    parts.append(f"{{\\c{highlight}}}{escaped}{{\\c{primary}}}")
                else:
                    parts.append(escaped)
            body = " ".join(parts)

            # Cue membentang sampai kata berikutnya mulai, agar tidak ada jeda.
            is_last_in_block = offset == len(block) - 1
            next_start = (
                clean[block_start + len(block)].start_s
                if is_last_in_block and block_start + len(block) < len(clean)
                else None
            )
            end_s = next_start if next_start is not None else max(active.end_s, active.start_s + 0.08)

            # Tag animasi ditaruh di DEPAN teks cue. libass menerapkan tag
            # di dalam {} pada teks yang mengikutinya, jadi urutannya penting:
            # animasi dulu, baru pewarnaan kata aktif.
            animation = _animation_tags(active_style, end_s - active.start_s)
            cue_text = f"{animation}{body}" if animation else body

            cues.append(SubtitleCue(text=cue_text, start_s=active.start_s, end_s=end_s))

    return cues


def render_ass(
    words: Sequence[SubtitleWord],
    width: int,
    height: int,
    style: SubtitleStyle | None = None,
    chunk_size: int | None = None,
    title: str = "ClipperAI captions",
) -> str:
    """Hasilkan isi berkas ASS lengkap siap dibakar FFmpeg.

    ``width`` dan ``height`` adalah resolusi NYATA video. Nilai ini dipakai
    sebagai ``PlayResX``/``PlayResY``; menulis nilai tetap di sini akan membuat
    subtitle gepeng pada klip non-9:16 (lihat catatan modul).

    Args:
        words: kata bertimestamp.
        width: lebar nyata video keluaran (PlayResX).
        height: tinggi nyata video keluaran (PlayResY).
        style: gaya visual; ``None`` memakai bawaan.
        chunk_size: jumlah kata per cue; ``None`` mengikuti ``style.chunk_size``.
        title: judul berkas ASS (muncul di metadata, bukan di video).
    """
    active_style = style or SubtitleStyle()
    cues = build_cues(words, chunk_size=chunk_size, style=active_style)

    header = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        _style_line(active_style, width, height),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    events = [
        (
            f"Dialogue: 0,{format_ass_time(cue.start_s)},{format_ass_time(cue.end_s)},"
            f"Default,,0,0,0,,{cue.text}"
        )
        for cue in cues
    ]

    return "\n".join(header + events) + "\n"


def collect_words(segments: Iterable[object]) -> list[SubtitleWord]:
    """Ratakan hasil transkripsi menjadi daftar kata.

    Menerima objek apa pun yang punya ``text``, ``start_s``, dan ``end_s`` —
    termasuk ``TranscriptWord`` dari :mod:`clipper_shared.stt`. Ditulis longgar
    agar generator ini tidak perlu mengimpor modul STT dan tetap ringan.
    """
    words: list[SubtitleWord] = []
    for segment in segments:
        text = getattr(segment, "text", None)
        start = getattr(segment, "start_s", None)
        end = getattr(segment, "end_s", None)
        if text is None or start is None or end is None:
            continue
        words.append(SubtitleWord(text=str(text), start_s=float(start), end_s=float(end)))
    words.sort(key=lambda w: (w.start_s, w.end_s))
    return words
