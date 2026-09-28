"""Guard panjang label segmen: tidak boleh melebihi kolom database.

Latar belakang — bug yang test ini cegah agar tidak kembali.

Batas 64 karakter (``clipper_shared.scoring.MAX_LABEL_CHARS``) harus sama
dengan kolom ``segments.label`` (``String(64)``). Pemotongan di
``normalize_segments`` dulu memakai ``[:80]``, sehingga label 65–80 karakter
lolos ke basis data.

Gejalanya muncul paling akhir di pipeline (setelah unduh 1 GB dan panggilan
LLM), jadi paling mahal untuk ditemukan lewat percobaan manual.

Test ini tidak memerlukan database: ia mengunci hubungan antara konstanta,
model, dan keluaran fungsi normalisasi.
"""

from __future__ import annotations

import pytest
from clipper_shared.scoring import MAX_LABEL_CHARS


def _raw_segment(**overrides: object) -> dict[str, object]:
    """Objek mentah bergaya keluaran LLM, dengan durasi yang lolos saringan."""
    base: dict[str, object] = {
        "start_s": 100.0,
        "end_s": 145.0,
        "score": 88.0,
        "label": "Label wajar",
        "hook_score": 0.8,
        "completeness": 0.7,
        "emotional_arc": 0.6,
        "reason": "Alasan singkat",
    }
    base.update(overrides)
    return base


class TestNormalizeSegmentsRespectsLimit:
    """Keluaran ``normalize_segments`` harus muat di kolom database.

    Fungsi ini membangun ``ScoredSegment`` (dataclass) sehingga TIDAK melewati
    validasi Pydantic. Karena itu batasnya harus ditegakkan di dalam fungsinya
    sendiri, dan di situlah ``[:80]`` dulu melubangi gerbang skema.
    """

    def test_label_panjang_dipotong_ke_batas_kolom(self) -> None:
        """Label 200 karakter harus keluar <= ``MAX_LABEL_CHARS``."""
        from worker_light.scoring_client import normalize_segments

        hasil = normalize_segments([_raw_segment(label="L" * 200)])
        assert len(hasil) == 1, "segmen valid seharusnya lolos saringan durasi"
        assert len(hasil[0].label) <= MAX_LABEL_CHARS, (
            f"label {len(hasil[0].label)} karakter melebihi batas kolom "
            f"{MAX_LABEL_CHARS} — INSERT akan gagal StringDataRightTruncation"
        )

    @pytest.mark.parametrize("panjang", [63, 64, 65, 80, 200])
    def test_semua_panjang_muat_di_kolom(self, panjang: int) -> None:
        """Batas harus ditegakkan untuk semua panjang, bukan hanya yang ekstrem.

        Nilai 65 dan 80 adalah kasus yang benar-benar gagal sebelum perbaikan:
        keduanya di bawah potongan lama (80) sehingga lolos, tetapi di atas
        kapasitas kolom (64).
        """
        from worker_light.scoring_client import normalize_segments

        hasil = normalize_segments([_raw_segment(label="Z" * panjang)])
        assert len(hasil) == 1
        assert len(hasil[0].label) <= MAX_LABEL_CHARS

    def test_alasan_dipotong_lebih_pendek_dari_kolom_text(self) -> None:
        """``reason`` masuk kolom TEXT sehingga tidak dibatasi 64.

        Dibatasi 400 karakter semata-mata untuk kewarasan (bukan batas skema);
        test ini memastikan pemotongan label tidak ikut memotong alasan.
        """
        from worker_light.scoring_client import normalize_segments

        hasil = normalize_segments([_raw_segment(reason="R" * 500)])
        assert len(hasil[0].reason) > MAX_LABEL_CHARS
