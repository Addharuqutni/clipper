"""Pengukuran presisi reframing terhadap keyframe berlabel.

Target OKR "presisi reframing >= 85%" (PRD §1.3) adalah **target, bukan hasil**
(TECH_SPEC §5.2; Sprint 3 butir 31). Modul ini menyediakan sisi matematisnya
supaya angka itu bisa diukur pada dataset klip berlabel:

* :func:`load_labels` membaca ``labels.json`` (lihat ``eval/reframe/README.md``);
* :func:`sample_crop_x` mengambil posisi crop yang berlaku pada detik tertentu
  dari lintasan per frame hasil :func:`worker_render.reframer.compute_crop_positions`;
* :func:`score_keyframe` memutuskan HIT/MISS satu keyframe;
* :func:`evaluate_clip` dan :class:`DatasetReport` mengagregasi per klip dan
  keseluruhan.

**Definisi HIT.** Label menyebut titik tengah horizontal wajah pembicara
(``cx``, 0..1 dari lebar video). Reframer dianggap benar pada keyframe itu bila
``cx`` berada **di dalam jendela crop 9:16** yang dipakai pada frame tersebut.
Selain HIT longgar itu, dihitung juga HIT bermargin: wajah harus berada di dalam
80% tengah crop (``margin_frac`` bawaan 0,2), yaitu kasus di mana wajah hampir
terpotong tepi layar.

**Mengapa modul ini terpisah dan tanpa FFmpeg/MediaPipe.** Keputusan HIT dan
agregasinya adalah bagian yang paling mudah salah (batas inklusif, margin,
crop lebih lebar daripada sumber) dan paling sulit diuji bila menempel pada
pemanggilan media. Semua fungsi di sini murni: masuk angka, keluar angka.
Pemanggilan reframer ada di ``scripts/eval_reframe.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: Nama berkas label di dalam direktori dataset.
LABELS_FILENAME = "labels.json"

#: Subdirektori berisi berkas klip (media berat, di-gitignore).
CLIPS_DIRNAME = "clips"

#: Margin bawaan: 0,2 dari lebar crop dibuang **dari kedua tepi sekaligus**
#: (0,1 kiri + 0,1 kanan) sehingga bagian tengah yang dihitung selebar 80%.
#: Wajah yang hanya "masih terlihat" di tepi crop belum tentu layak — dengan
#: margin ini wajah yang mepet potong dihitung MISS.
DEFAULT_MARGIN_FRAC = 0.20

#: Target PRD §1.3. Angka hasil ukur baru boleh diklaim bila >= nilai ini.
TARGET_PRECISION = 0.85

#: TECH_SPEC §5.2: "Ukur dulu pada 20 klip berlabel, baru klaim angkanya."
MIN_CLIPS_FOR_CLAIM = 20


@dataclass(frozen=True, slots=True)
class KeyframeLabel:
    """Satu titik label.

    Attributes:
        t: Detik pada klip (dihitung dari awal berkas klip).
        cx: Titik tengah horizontal wajah pembicara AKTIF, ternormalisasi
            terhadap lebar video (0 = tepi kiri, 1 = tepi kanan).
    """

    t: float
    cx: float


@dataclass(frozen=True, slots=True)
class ClipLabel:
    """Label satu klip: nama berkas + daftar keyframe-nya."""

    clip: str
    keyframes: tuple[KeyframeLabel, ...]


@dataclass(frozen=True, slots=True)
class KeyframeScore:
    """Hasil penilaian satu keyframe.

    ``crop_x``/``crop_w`` adalah jendela crop EFEKTIF yang dipakai penilaian
    (sudah dijepit ke dalam bingkai sumber bila sumber lebih sempit dari crop).
    """

    t: float
    cx: float
    #: Posisi wajah dalam piksel sumber (``cx * source_width``), untuk laporan.
    x_px: float
    crop_x: int
    crop_w: int
    source_width: int
    #: Wajah berada di dalam jendela crop (batas tepi dihitung HIT).
    hit: bool
    #: Wajah berada di dalam (1 - margin_frac) tengah jendela crop.
    margin_hit: bool


@dataclass(frozen=True, slots=True)
class ClipEvaluation:
    """Hasil penilaian satu klip terhadap lintasan crop reframer."""

    clip: str
    source_width: int
    source_height: int
    fps: float
    crop_w: int
    #: Ringkasan kesehatan pelacakan dari :meth:`worker_render.face_tracker.FaceTracker.tracking_health`.
    tracking_health: str
    #: True bila reframer tidak menemukan wajah sama sekali (crop tengah statis).
    no_face: bool
    scores: tuple[KeyframeScore, ...]

    @property
    def keyframes_total(self) -> int:
        """Jumlah keyframe yang dinilai."""
        return len(self.scores)

    @property
    def hits(self) -> int:
        """Jumlah keyframe HIT (wajah di dalam jendela crop)."""
        return sum(1 for score in self.scores if score.hit)

    @property
    def margin_hits(self) -> int:
        """Jumlah keyframe HIT bermargin (di dalam tengah ``1 - margin_frac`` crop)."""
        return sum(1 for score in self.scores if score.margin_hit)

    @property
    def precision(self) -> float:
        """Hit rate klip ini (0..1); 0.0 bila tidak ada keyframe."""
        return self.hits / self.keyframes_total if self.scores else 0.0

    @property
    def margin_precision(self) -> float:
        """Hit rate bermargin klip ini (0..1); 0.0 bila tidak ada keyframe."""
        return self.margin_hits / self.keyframes_total if self.scores else 0.0


@dataclass(frozen=True, slots=True)
class DatasetReport:
    """Agregat seluruh dataset; inilah angka "presisi reframing"."""

    dataset: str
    margin_frac: float
    clips: tuple[ClipEvaluation, ...]

    @property
    def clips_total(self) -> int:
        """Jumlah klip yang dinilai (bukan jumlah label bila difilter)."""
        return len(self.clips)

    @property
    def keyframes_total(self) -> int:
        """Jumlah keyframe yang dinilai."""
        return sum(clip.keyframes_total for clip in self.clips)

    @property
    def hits_total(self) -> int:
        """Jumlah keyframe HIT."""
        return sum(clip.hits for clip in self.clips)

    @property
    def margin_hits_total(self) -> int:
        """Jumlah keyframe HIT bermargin."""
        return sum(clip.margin_hits for clip in self.clips)

    @property
    def precision(self) -> float:
        """Presisi keseluruhan (0..1): total HIT / total keyframe."""
        return self.hits_total / self.keyframes_total if self.keyframes_total else 0.0

    @property
    def margin_precision(self) -> float:
        """Presisi bermargin keseluruhan (0..1)."""
        return self.margin_hits_total / self.keyframes_total if self.keyframes_total else 0.0

    @property
    def clips_without_face(self) -> int:
        """Jumlah klip yang seluruhnya tidak menemukan wajah."""
        return sum(1 for clip in self.clips if clip.no_face)

    @property
    def claimable(self) -> bool:
        """True bila jumlah klip memenuhi syarat minimal untuk mengklaim angka."""
        return self.clips_total >= MIN_CLIPS_FOR_CLAIM

    @property
    def meets_target(self) -> bool:
        """True bila presisi mencapai target PRD (``TARGET_PRECISION``)."""
        return self.precision >= TARGET_PRECISION


def load_labels(dataset_dir: Path) -> tuple[ClipLabel, ...]:
    """Baca ``<dataset_dir>/labels.json`` menjadi label bertipe.

    Args:
        dataset_dir: Direktori dataset (``eval/reframe`` secara bawaan).

    Raises:
        FileNotFoundError: Berkas label tidak ada.
        ValueError: JSON tidak valid atau bentuknya tidak sesuai skema.
    """
    path = dataset_dir / LABELS_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"Berkas label tidak ditemukan: {path}. "
            "Buat labels.json berisi daftar klip berlabel (lihat eval/reframe/README.md)."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} bukan JSON yang valid: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(
            f"{path} harus berisi daftar (list) klip, bukan {type(raw).__name__}."
        )

    return tuple(_parse_clip(entry, index, path) for index, entry in enumerate(raw))


def _parse_clip(entry: object, index: int, source: Path) -> ClipLabel:
    """Ubah satu elemen ``labels.json`` menjadi :class:`ClipLabel`."""
    where = f"{source} klip #{index + 1}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: setiap klip harus berupa objek JSON.")

    clip = entry.get("clip")
    if not isinstance(clip, str) or not clip.strip():
        raise ValueError(f'{where}: kolom "clip" wajib berisi nama berkas, mis. "video.mp4".')

    keyframes_raw = entry.get("keyframes")
    if not isinstance(keyframes_raw, list) or not keyframes_raw:
        raise ValueError(f'{where}: kolom "keyframes" wajib berisi minimal satu titik.')

    keyframes = tuple(
        _parse_keyframe(item, f"{where}, keyframe #{position + 1}")
        for position, item in enumerate(keyframes_raw)
    )
    return ClipLabel(clip=clip.strip(), keyframes=keyframes)


def _parse_keyframe(entry: object, where: str) -> KeyframeLabel:
    """Ubah satu keyframe JSON menjadi :class:`KeyframeLabel` dengan validasi."""
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: setiap keyframe harus berupa objek JSON.")

    t = _number(entry.get("t"), f"{where}.t")
    cx = _number(entry.get("cx"), f"{where}.cx")
    if t < 0:
        raise ValueError(f"{where}.t harus >= 0 detik, bukan {t}.")
    if not 0.0 <= cx <= 1.0:
        raise ValueError(f"{where}.cx harus pada 0..1 (fraksi lebar video), bukan {cx}.")
    return KeyframeLabel(t=t, cx=cx)


def _number(value: object, where: str) -> float:
    """Ambil angka dari JSON; ``bool`` ditolak walau di Python ia subclass int."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} harus berupa angka, bukan {value!r}.")
    return float(value)


def frame_index_for(t: float, fps: float, frame_count: int) -> int:
    """Indeks frame yang tampil pada detik ``t`` — yaitu ``floor(t * fps)``.

    Epsilon 1e-6 dipakai supaya label yang dibulatkan ke milidetik (mis. 4.200
    pada 30 fps) tidak jatuh ke frame sebelumnya hanya karena pembulatan
    floating point. Hasilnya selalu dijepit ke rentang frame yang tersedia.
    """
    if frame_count <= 0:
        raise ValueError("frame_count harus positif.")
    if fps <= 0:
        raise ValueError("fps harus positif.")
    index = int(t * fps + 1e-6)
    return max(0, min(index, frame_count - 1))


def sample_crop_x(positions: Sequence[int], fps: float, t: float) -> int:
    """Posisi crop (piksel) yang berlaku pada detik ``t``.

    ``positions`` adalah lintasan per frame hasil reframer untuk klip ini,
    dimulai dari frame 0. Posisi pada frame ``i`` berlaku sampai frame
    berikutnya — persis seperti efek ``sendcmd``, yang hanya menulis nilai saat
    posisinya berubah.

    Raises:
        ValueError: ``positions`` kosong (reframer tidak menemukan wajah).
    """
    if not positions:
        raise ValueError("positions kosong: tidak ada lintasan crop untuk disampel.")
    return positions[frame_index_for(t, fps, len(positions))]


def centered_crop_x(source_width: int, crop_w: int) -> int:
    """Posisi crop tengah statis.

    Ini fallback reframer saat tidak ada wajah sama sekali di seluruh klip
    (``crop=...:(in_w-out_w)/2:0`` di ``build_video_filter``), jadi penilaian
    harus memakai lintasan ini agar mencerminkan perilaku nyata.
    """
    return max(0, (source_width - crop_w) // 2)


def score_keyframe(
    cx: float,
    *,
    t: float,
    crop_x: int,
    crop_w: int,
    source_width: int,
    margin_frac: float = DEFAULT_MARGIN_FRAC,
) -> KeyframeScore:
    """Putuskan HIT/MISS satu keyframe terhadap jendela crop yang dipakai.

    Batas tepi dihitung **HIT** (perbandingan inklusif di kedua sisi). Bila crop
    lebih lebar daripada sumber, jendela efektif adalah seluruh lebar sumber
    dan posisi crop dijepit ke 0 — begitulah filter ``crop`` berperilaku.

    Args:
        cx: Titik tengah wajah yang dilabeli, 0..1 dari lebar sumber.
        t: Detik keyframe (dibawa apa adanya untuk laporan).
        crop_x: Posisi kiri crop pada frame itu, dalam piksel sumber.
        crop_w: Lebar crop dalam piksel (dari ``crop_width_for``).
        source_width: Lebar video sumber dalam piksel (setelah rotasi tampilan).
        margin_frac: Total lebar crop yang dibuang dari kedua tepi untuk HIT
            bermargin; harus pada 0..<1. Bawaan 0,2 berarti wajah harus berada
            di dalam 80% tengah jendela crop.

    Raises:
        ValueError: Argumen di luar rentang yang masuk akal.
    """
    if source_width <= 0:
        raise ValueError("source_width harus positif.")
    if crop_w <= 0:
        raise ValueError("crop_w harus positif.")
    if not 0.0 <= margin_frac < 1.0:
        raise ValueError(f"margin_frac harus pada 0..1, bukan {margin_frac}.")
    if not 0.0 <= cx <= 1.0:
        raise ValueError(f"cx harus pada 0..1, bukan {cx}.")

    effective_w = min(crop_w, source_width)
    left = max(0, min(crop_x, source_width - effective_w))
    right = left + effective_w
    x_px = cx * source_width

    hit = left <= x_px <= right
    inset = margin_frac / 2.0 * effective_w
    margin_hit = hit and (left + inset) <= x_px <= (right - inset)

    return KeyframeScore(
        t=t,
        cx=cx,
        x_px=x_px,
        crop_x=left,
        crop_w=effective_w,
        source_width=source_width,
        hit=hit,
        margin_hit=margin_hit,
    )


def evaluate_clip(
    label: ClipLabel,
    *,
    positions: Sequence[int],
    fps: float,
    source_width: int,
    source_height: int,
    crop_w: int,
    tracking_health: str = "",
    margin_frac: float = DEFAULT_MARGIN_FRAC,
) -> ClipEvaluation:
    """Nilai seluruh keyframe satu klip terhadap lintasan crop hasil reframer.

    Args:
        label: Label klip (nama + keyframe).
        positions: Lintasan posisi crop per frame dari
            :func:`worker_render.reframer.compute_crop_positions`. Kosong berarti
            tidak ada wajah sama sekali dan crop memakai posisi tengah statis.
        fps: Laju frame video sumber.
        source_width: Lebar sumber dalam piksel.
        source_height: Tinggi sumber dalam piksel (untuk laporan).
        crop_w: Lebar crop 9:16 untuk sumber ini (``crop_width_for``).
        tracking_health: Ringkasan kesehatan pelacakan dari reframer.
        margin_frac: Total lebar crop yang dibuang dari kedua tepi untuk HIT
            bermargin (bawaan 0,2 = wajah di dalam 80% tengah crop).
    """
    no_face = len(positions) == 0
    fallback_x = centered_crop_x(source_width, crop_w)
    scores = tuple(
        score_keyframe(
            keyframe.cx,
            t=keyframe.t,
            crop_x=fallback_x if no_face else sample_crop_x(positions, fps, keyframe.t),
            crop_w=crop_w,
            source_width=source_width,
            margin_frac=margin_frac,
        )
        for keyframe in label.keyframes
    )
    return ClipEvaluation(
        clip=label.clip,
        source_width=source_width,
        source_height=source_height,
        fps=fps,
        crop_w=crop_w,
        tracking_health=tracking_health,
        no_face=no_face,
        scores=scores,
    )
