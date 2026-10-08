"""Pengukuran kualitas pemilihan momen terhadap dataset berlabel.

Modul ini adalah padanan :mod:`clipper_shared.reframe_eval`, tetapi untuk
**pemilihan momen**, bukan reframing. Beda yang penting: reframing punya satu
jawaban benar per keyframe, sedangkan pemilihan momen tidak — dua orang bisa
menunjuk momen yang berbeda di video yang sama dan keduanya sama-sama bagus.
Karena itu metriknya berbasis **tumpang tindih rentang waktu**, bukan
kecocokan titik.

**Definisi HIT.** Satu segmen hasil AI dihitung HIT bila tumpang tindih
waktunya dengan satu momen berlabel mencapai ``IoU >= IOU_THRESHOLD`` (0,5 —
konvensi deteksi objek). Satu momen berlabel hanya boleh dipakai sekali;
bila dua prediksi memperebutkan momen yang sama, yang **skor lebih tinggi**
menang (greedy), karena urutan skor adalah klaim utama sistem.

**Mengapa modul ini murni dan tanpa LLM.** Keputusan HIT dan agregasinya
adalah bagian yang paling mudah salah (batas inklusif, urutan greedy, rentang
yang tidak valid) dan paling sulit diuji bila menempel pada pemanggilan jaringan.
Semua fungsi di sini murni: masuk angka, keluar angka. Pemanggilan LLM ada di
``scripts/eval_scoring.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "IOU_THRESHOLD",
    "MIN_CASES_FOR_CLAIM",
    "CaseResult",
    "DatasetReport",
    "ExpectedMoment",
    "ScoringCase",
    "aggregate",
    "evaluate_case",
    "intersection_over_union",
    "load_case",
    "load_dataset",
]

#: Ambang tumpang tindih agar sebuah prediksi dihitung benar.
#:
#: 0,5 adalah konvensi deteksi objek (PASCAL VOC). Untuk klip video, IoU 0,5
#: berarti misalnya prediksi 40 detik dan label 40 detik bergeser paling banyak
#: 20 detik — masih "momen yang sama", walaupun batasnya tidak presisi.
IOU_THRESHOLD = 0.5

#: Meniru aturan reframing (TECH_SPEC §5.2): angka baru boleh diklaim setelah
#: sekian banyak kasus berlabel, supaya tidak mengklaim perbaikan dari noise.
MIN_CASES_FOR_CLAIM = 20

#: IoU di bawah ini dihitung "lemah": kasusnya tetap lolos ambang HIT, tetapi
#: selisih rentangnya nyata. Melaporkannya terpisah mencegah "presisi 100%"
#: dibaca sebagai kesempurnaan.
WEAK_IOU = 0.7

#: Nama berkas kasus.
CASE_SUFFIX = ".json"


@dataclass(frozen=True, slots=True)
class ExpectedMoment:
    """Satu momen yang menurut manusia layak diklip.

    Attributes:
        start_s: Awal momen, detik dari awal video.
        end_s: Akhir momen, detik dari awal video.
        note: Catatan bebas penjelas (untuk manusia, tidak dipakai metrik).
    """

    start_s: float
    end_s: float
    note: str = ""

    @property
    def duration_s(self) -> float:
        """Lama momen dalam detik."""
        return self.end_s - self.start_s

    def is_valid(self) -> bool:
        """Apakah rentangnya masuk akal.

        Returns:
            ``True`` bila akhir sesudah awal dan keduanya tidak negatif.
        """
        return self.end_s > self.start_s and self.start_s >= 0.0


@dataclass(frozen=True, slots=True)
class ScoringCase:
    """Satu kasus uji: transkrip + momen yang diharapkan.

    Attributes:
        slug: Pengenal kasus, dipakai di laporan.
        transcript: Transkrip bertimestamp, format sama dengan keluaran
            ``_render_transcript`` (baris ``[12.4s] teks ...``). Disimpan
            dalam format jadi, bukan daftar kata, supaya eval tidak bergantung
            pada kode rendering transkrip.
        expected: Momen yang menurut manusia layak diklip.
        target_count: Jumlah klip yang diminta dari sistem.
        video_duration_s: Durasi video; dipakai untuk memotong prediksi liar.
    """

    slug: str
    transcript: str
    expected: tuple[ExpectedMoment, ...] = ()
    target_count: int = 5
    video_duration_s: float | None = None


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Hasil satu kasus.

    Attributes:
        slug: Pengenal kasus.
        predicted_count: Jumlah segmen yang diajukan sistem.
        expected_count: Jumlah momen berlabel.
        hits: Prediksi yang cocok dengan momen berlabel.
        matched: Indeks momen berlabel yang berhasil ditemukan.
        missed: Indeks momen berlabel yang tidak ditemukan.
        spurious: Prediksi yang tidak cocok dengan momen mana pun.
        error: Isi bila kasus gagal dijalankan; kasus gagal tidak masuk
            hitungan presisi/recall.
        repeats: Berapa kali kasus dijalankan. 1 berarti satu pemanggilan.
        majority_votes: Berapa dari ``repeats`` yang sepakat pada hasil ini.
        stable: ``True`` bila semua percobaan sepakat. ``False`` berarti
            model tidak konsisten pada kasus ini — informasi berharga, bukan
            sekadar noise yang bisa dibuang.
    """

    slug: str
    predicted_count: int = 0
    expected_count: int = 0
    hits: int = 0
    matched: tuple[int, ...] = ()
    missed: tuple[int, ...] = ()
    #: IoU tertinggi yang dicapai kasus ini. Jauh lebih informatif daripada
    #: HIT/MISS: ambang 0,5 itu longgar, sehingga klip yang seluruhnya
    #: berlabel akan "HIT" hampir selalu dan presisi 100% bisa menyembunyikan
    #: selisih yang nyata.
    best_iou: float = 0.0
    spurious: tuple[tuple[float, float], ...] = ()
    error: str = ""
    #: Berapa kali kasus dijalankan (1 = sekali). Field ini tidak memengaruhi
    #: perhitungan mana pun; hanya untuk melaporkan ketidakstabilan.
    repeats: int = 1
    #: Berapa dari ``repeats`` yang sepakat pada hasil ini.
    majority_votes: int = 1
    #: ``True`` bila semua percobaan sepakat.
    stable: bool = True

    @property
    def precision(self) -> float:
        """Dari yang diajukan, berapa yang benar-benar bagus."""
        return self.hits / self.predicted_count if self.predicted_count else 0.0

    @property
    def recall(self) -> float:
        """Dari momen bagus yang ada, berapa yang ketemu."""
        return self.hits / self.expected_count if self.expected_count else 0.0

    @property
    def f1(self) -> float:
        """Harmonisasi precision dan recall; 0 bila salah satunya 0."""
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total > 0 else 0.0


@dataclass(slots=True)
class DatasetReport:
    """Ringkasan seluruh dataset.

    Agregasi dilakukan **per total segmen**, bukan rata-rata per kasus — sama
    seperti :mod:`clipper_shared.reframe_eval`, supaya kasus dengan 2 momen
    tidak berbobot sama dengan kasus ber-20 momen.
    """

    cases: list[CaseResult] = field(default_factory=list)
    iou_threshold: float = IOU_THRESHOLD

    @property
    def cases_total(self) -> int:
        """Jumlah kasus yang berhasil dijalankan."""
        return len(self.cases)

    @property
    def cases_failed(self) -> int:
        """Jumlah kasus yang gagal dijalankan."""
        return sum(1 for case in self.cases if case.error)

    @property
    def predicted_total(self) -> int:
        """Total segmen yang diajukan ke seluruh kasus."""
        return sum(case.predicted_count for case in self.cases if not case.error)

    @property
    def expected_total(self) -> int:
        """Total momen berlabel di seluruh kasus."""
        return sum(case.expected_count for case in self.cases if not case.error)

    @property
    def hits_total(self) -> int:
        """Total prediksi yang cocok."""
        return sum(case.hits for case in self.cases if not case.error)

    @property
    def precision(self) -> float:
        """Presisi teragregasi."""
        return self.hits_total / self.predicted_total if self.predicted_total else 0.0

    @property
    def recall(self) -> float:
        """Recall teragregasi."""
        return self.hits_total / self.expected_total if self.expected_total else 0.0

    @property
    def f1(self) -> float:
        """F1 teragregasi."""
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total > 0 else 0.0

    @property
    def claimable(self) -> bool:
        """Apakah angka ini sudah boleh diklaim.

        Returns:
            ``True`` bila jumlah kasus berlabel mencapai
            :data:`MIN_CASES_FOR_CLAIM`.
        """
        return self.cases_total >= MIN_CASES_FOR_CLAIM

    @property
    def unstable_cases(self) -> tuple[str, ...]:
        """Kasus yang tidak memberi hasil sama di semua percobaan.

        Kasus seperti ini membuat angka agregata bergeser antar evaluasi tanpa
        ada perubahan kode. Melaporkannya secara terbuka lebih jujur daripada
        menyembunyikannya di balik rata-rata.
        """
        return tuple(case.slug for case in self.cases if not case.stable)

    @property
    def median_iou(self) -> float:
        """IoU median seluruh kasus.

        Lebih jujur daripada presisi: ambang HIT 0,5 itu longgar, sehingga
        "100% HIT" belum berarti AI selalu memilih rentang yang sama dengan
        penilaian manusia.
        """
        values = sorted(case.best_iou for case in self.cases if not case.error)
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2.0

    @property
    def min_iou(self) -> float:
        """IoU terendah di antara kasus yang berhasil."""
        values = [case.best_iou for case in self.cases if not case.error]
        return min(values) if values else 0.0

    @property
    def weak_cases_count(self) -> int:
        """Jumlah kasus dengan IoU di bawah ``WEAK_IOU``.

        Kasus seperti ini lolos ambang HIT tetapi selisihnya nyata — itulah
        yang tidak terlihat ketika hanya membaca presisi dan recall.
        """
        return sum(
            1 for case in self.cases if not case.error and case.best_iou < WEAK_IOU
        )


def intersection_over_union(
    a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Tumpang tindih dua rentang waktu dibagi gabungannya.

    Args:
        a: Rentang pertama ``(start, end)``.
        b: Rentang kedua ``(start, end)``.

    Returns:
        Nilai 0..1. Nol bila tidak bersinggungan atau gabungan kosong.
    """
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def evaluate_case(
    case: ScoringCase,
    predicted: list[tuple[float, float]],
    *,
    scores: list[float] | None = None,
    iou_threshold: float = IOU_THRESHOLD,
) -> CaseResult:
    """Nilai satu kasus: cocokkan prediksi dengan momen berlabel.

    Pencocokan dilakukan **greedy berdasar skor**: prediksi diurutkan dari skor
    tertinggi, tiap prediksi diuji ke momen berlabel yang belum terpakai, dan
    momen dengan IoU tertinggi yang masih lolos ambang dipakai. Greedy dipilih
    karena urutan skor adalah klaim utama sistem — bila prediksi terbaik
    sistem ternyata cocok dengan momen bagus, itu harus dihitung sebagai
    keberhasilan.

    Args:
        case: Kasus berlabel.
        predicted: Rentang yang diajukan sistem.
        scores: Skor tiap prediksi; bila ``None``, urutan masukan dipakai.
        iou_threshold: Ambang IoU agar dihitung HIT.

    Returns:
        :class:`CaseResult` dengan hitungan dan daftar yang cocok/terlewat/liar.
    """
    ranked = list(range(len(predicted)))
    if scores is not None and len(scores) == len(predicted):
        ranked.sort(key=lambda index: scores[index], reverse=True)
    else:
        ranked.sort(key=lambda index: predicted[index])

    used: set[int] = set()
    matched: list[int] = []
    spurious: list[tuple[float, float]] = []
    #: IoU terbaik di seluruh kasus, bukan hanya yang yang lolos ambang —
    #: inilah yang membedakan "tepat sasaran" dari "tepat minimum 0,5".
    peak_iou = 0.0

    for index in ranked:
        window = predicted[index]
        best_index = -1
        best_iou = 0.0
        for moment_index, moment in enumerate(case.expected):
            if moment_index in used:
                continue
            iou = intersection_over_union(window, (moment.start_s, moment.end_s))
            if iou > best_iou:
                best_iou = iou
                best_index = moment_index

        peak_iou = max(peak_iou, best_iou)

        if best_index >= 0 and best_iou >= iou_threshold:
            used.add(best_index)
            matched.append(best_index)
        else:
            spurious.append(window)

    missed = tuple(i for i in range(len(case.expected)) if i not in used)

    return CaseResult(
        slug=case.slug,
        predicted_count=len(predicted),
        expected_count=len(case.expected),
        hits=len(matched),
        matched=tuple(sorted(matched)),
        missed=missed,
        best_iou=peak_iou,
        spurious=tuple(spurious),
    )


def aggregate(results: list[CaseResult], *, iou_threshold: float = IOU_THRESHOLD) -> DatasetReport:
    """Gabungkan hasil per kasus menjadi satu laporan.

    Args:
        results: Hasil :func:`evaluate_case`.
        iou_threshold: Ambang IoU yang dipakai, untuk direkam di laporan.

    Returns:
        :class:`DatasetReport` siap cetak.
    """
    report = DatasetReport(iou_threshold=iou_threshold)
    report.cases.extend(results)
    return report


def load_case(path: Path) -> ScoringCase:
    """Baca satu berkas kasus.

    Args:
        path: Berkas JSON kasus.

    Returns:
        :class:`ScoringCase`.

    Raises:
        ValueError: bila struktur berkas tidak sah.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: berkas kasus tidak dapat dibaca: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: akar berkas harus objek JSON.")

    transcript = raw.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError(f"{path.name}: 'transcript' wajib diisi dan tidak kosong.")

    expected: list[ExpectedMoment] = []
    for item in raw.get("expected") or []:
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}: tiap entri 'expected' harus objek.")
        raw_start = item.get("start_s")
        raw_end = item.get("end_s")
        if isinstance(raw_start, bool) or isinstance(raw_end, bool):
            raise ValueError(f"{path.name}: start_s/end_s harus angka, bukan boolean.")
        try:
            start_s = float(raw_start)  # type: ignore[arg-type]
            end_s = float(raw_end)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path.name}: start_s/end_s harus angka.") from exc
        moment = ExpectedMoment(
            start_s=start_s,
            end_s=end_s,
            note=str(item.get("note") or ""),
        )
        if not moment.is_valid():
            raise ValueError(f"{path.name}: rentang tidak sah {start_s}-{end_s}.")
        expected.append(moment)

    return ScoringCase(
        slug=str(raw.get("slug") or path.stem),
        transcript=transcript,
        expected=tuple(expected),
        target_count=int(raw.get("target_count") or 5),
        video_duration_s=(
            float(raw["video_duration_s"])
            if raw.get("video_duration_s") is not None
            else None
        ),
    )


def load_dataset(directory: Path) -> list[ScoringCase]:
    """Baca semua berkas kasus dalam satu direktori.

    Args:
        directory: Direktori berisi berkas ``*.json``.

    Returns:
        Daftar kasus, urut menaik berdasar nama berkas.
    """
    if not directory.is_dir():
        return []
    cases: list[ScoringCase] = []
    for path in sorted(directory.glob(f"*{CASE_SUFFIX}")):
        if path.name.endswith(".example"):
            continue
        cases.append(load_case(path))
    return cases
