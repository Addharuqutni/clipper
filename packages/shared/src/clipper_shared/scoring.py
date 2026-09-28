"""Batas kandidat klip yang dipakai API dan worker-light.

Validasi output LLM sendiri ada di ``worker_light.scoring_client``.
"""

from __future__ import annotations

#: Batas durasi klip per TECH_SPEC §6 Sprint 2 butir 21.
MIN_SEGMENT_S = 30.0
MAX_SEGMENT_S = 60.0

#: Jumlah kandidat yang diminta ke LLM.
MIN_SEGMENTS = 1
MAX_SEGMENTS = 30
#: Kerapatan klip maksimum: 1 klip per 3 menit video. Klip 30–60 detik perlu
#: jeda di antaranya; lebih rapat dari ini berarti sebagian besar video menjadi
#: klip dan AI terpaksa mengisi kuota dengan momen lemah.
MINUTES_PER_CLIP = 3


def max_clips_for_duration(duration_s: float) -> int:
    """Jumlah klip maksimum untuk video sepanjang ``duration_s``."""
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, int(duration_s // (MINUTES_PER_CLIP * 60))))

#: Panjang maksimum label segmen. HARUS sama dengan kolom ``segments.label``
#: (``sa.String(length=64)`` di migrasi 0001). Dijadikan konstanta — bukan angka
#: lepas — karena sebelumnya nilai ini muncul terpisah di tiga tempat (model di
#: sini, pemotongan di ``scoring_client``, dan kolom DB) dan ketiganya
#: menyimpang: model bilang 64, pemotongan memakai 80, kolom 64. Akibatnya
#: INSERT gagal ``StringDataRightTruncation`` pada label 65–80 karakter.
MAX_LABEL_CHARS = 64
