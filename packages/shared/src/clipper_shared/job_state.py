"""Status, tahap, dan skala progres sebuah job — satu tempat untuk semuanya.

Sebelum modul ini, pengetahuan tentang siklus hidup job tersebar: nilai status
ditulis sebagai string di lima tempat, pembaca mengulang predikat yang sama, dan
skala progres 0–100 tidak dimiliki siapa pun (angka seperti ``75 + int(25*n/t)``
dan ``TRANSCRIBE_START = 20`` hidup di modul worker masing-masing).

Isi modul ini:

* **Kosakata** — nilai status dan tahap yang sah. ``jobs.status`` punya CHECK
  constraint di basis data; :data:`JOB_STATUSES` adalah cermin Python-nya, dan
  ``apps/api/tests/test_frontend_types.py`` menjaga keduanya tetap sama.
* **Predikat** — :func:`is_active` dan :func:`is_terminal`, dipakai API dan
  worker. Tanpa ini setiap pemanggil menulis ulang ``status in {...}`` dengan
  daftar yang bisa berbeda-beda.
* **Skala progres** — :func:`progress` memetakan (tahap, pecahan) ke 0–100.
  Pemanggil cukup menyebut "sudah 30% di tahap render"; angkanya diurus di sini
  sehingga bilah progres tidak pernah mundur atau melompati 100.
* **Hasil akhir** — :class:`RenderSummary` memutuskan status job dari status
  render terakhir tiap segmen. Keputusan ini menyangkut nasib job, bukan
  mekanika render, jadi tempatnya di sini.

Modul ini **tidak menyentuh basis data**. Penulisnya adalah
:func:`clipper_shared.worker_events.emit` (worker) dan ORM API (untuk
membuat/membatalkan job) — keduanya memakai kosakata dari sini.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Nilai ``jobs.status``. Cermin CHECK constraint ``status_valid`` di
#: ``app.models.job``; basis data yang menolak nilai salah saat runtime.
JOB_STATUSES: tuple[str, ...] = ("queued", "running", "done", "failed", "canceled")

#: Tahap pipeline, berurutan. Cermin ``JobStage`` di ``apps/web/lib/types.ts``.
JOB_STAGES: tuple[str, ...] = ("upload", "ingest", "transcribe", "analyze", "render", "done")

#: Job yang masih menunggu atau berjalan. Dipakai untuk menolak aksi ganda
#: (mengirim ulang job, membatalkan dua kali) dan untuk mengenali job yatim.
ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})

#: Job yang sudah berakhir; tidak ada transisi keluar dari sini.
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "canceled"})

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELED = "canceled"

UPLOAD = "upload"
INGEST = "ingest"
TRANSCRIBE = "transcribe"
ANALYZE = "analyze"
RENDER = "render"

#: Pita progres per tahap: ``(bawah, atas)`` dalam 0–100. Tahap yang lebih awal
#: selalu berada di bawah tahap berikutnya, jadi bilah progres tidak pernah
#: mundur saat pipeline berpindah tahap. ``done`` selalu 100.
#:
#: Lebar pita mencerminkan porsi waktu yang sebenarnya: transkripsi Whisper di
#: CPU memakan waktu paling lama (D1), jadi pitanya paling lebar.
_BANDS: dict[str, tuple[int, int]] = {
    UPLOAD: (0, 1),
    INGEST: (2, 18),
    TRANSCRIBE: (20, 65),
    ANALYZE: (66, 74),
    RENDER: (75, 99),
    DONE: (100, 100),
}

def is_active(status: str | None) -> bool:
    """Benar bila job masih menunggu atau berjalan."""
    return status in ACTIVE_STATUSES

def is_terminal(status: str | None) -> bool:
    """Benar bila job sudah berakhir (selesai, gagal, atau dibatalkan)."""
    return status in TERMINAL_STATUSES

def progress(stage: str, fraction: float = 0.0) -> int:
    """Progres 0–100 untuk posisi ``fraction`` (0..1) di dalam ``stage``.

    Raises:
        ValueError: tahap tidak dikenal. Tahap berasal dari kode, bukan masukan
            pengguna, jadi nilai asing adalah bug yang harus terlihat — bukan
            diam-diam dipetakan ke 0.
    """
    if stage not in _BANDS:
        raise ValueError(f"Tahap tidak dikenal: {stage!r}; pilihan: {', '.join(JOB_STAGES)}")
    low, high = _BANDS[stage]
    clamped = min(1.0, max(0.0, fraction))
    return low + round((high - low) * clamped)

@dataclass(frozen=True, slots=True)
class RenderSummary:
    """Hasil render terbaru per segmen, tanpa render yang dibatalkan.

    Render ``canceled`` dihentikan pengguna: bukan hasil, bukan kegagalan. Ia
    dikeluarkan dari ``total`` juga — kalau tidak, segmen yang dibatalkan ikut
    menjadi penyebut dan pesan akhir berbunyi "0 render gagal".
    """

    pending: int
    done: int
    failed: int

    @property
    def finished(self) -> int:
        """Render yang sudah selesai, berhasil maupun gagal."""
        return self.done + self.failed

    @property
    def total(self) -> int:
        """Semua render yang dihitung, termasuk yang masih tertunda."""
        return self.pending + self.finished

    @property
    def fraction(self) -> float:
        """Bagian render yang sudah selesai (0..1) — untuk progres tahap render."""
        return self.finished / self.total if self.total else 0.0

    def final_outcome(self) -> tuple[str, str]:
        """Status job dan pesan untuk pengguna setelah tidak ada render tertunda.

        Sebagian berhasil tetap ``done``: klip yang gagal bisa dirender ulang,
        dan menandai seluruh job gagal karena satu klip membuat sembilan klip
        bagus ikut tampil sebagai kegagalan.
        """
        if self.done and self.failed:
            return DONE, f"{self.done} klip selesai, {self.failed} gagal (bisa dirender ulang)."
        if self.done:
            return DONE, f"{self.done} klip selesai."
        if self.failed:
            return FAILED, f"Semua {self.failed} render gagal. Lihat log untuk detail."
        return DONE, "Tidak ada klip yang dirender (semua render dibatalkan)."

def render_summary(statuses: list[str]) -> RenderSummary:
    """Ringkas status render terbaru tiap segmen menjadi :class:`RenderSummary`."""
    return RenderSummary(
        pending=sum(is_active(status) for status in statuses),
        done=statuses.count(DONE),
        failed=statuses.count(FAILED),
    )
