"""Abstraksi STT — hedge agar keputusan D1 (CPU-only self-host) bisa dibalik.

TECH_SPEC §5.1: ``Transcriber`` adalah Protocol, dengan dua implementasi yang
dipilih lewat env ``STT_BACKEND=local|remote``. Ini "satu-satunya hedge yang
membuat D1 dapat dibalik tanpa penulisan ulang".

Catatan penting: modul ini TIDAK meng-import ``faster_whisper`` di top-level.
Import dilakukan lazy di dalam method supaya API (container A) bisa meng-import
kontrak ini tanpa menarik ctranslate2/ctranslate2-CPU wheel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "FasterWhisperLocal",
    "RemoteWhisperAPI",
    "TranscriptResult",
    "TranscriptWord",
    "Transcriber",
    "get_transcriber",
]


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """Satu kata hasil STT dengan timestamp word-level.

    Word-level timing wajib: karaoke subtitle per-kata dibangun dari data ini,
    dan `score_segments` butuh boundary kata agar cut tidak memotong di tengah
    kalimat.
    """

    start_s: float
    end_s: float
    word: str
    probability: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """Hasil transkripsi lengkap satu media.

    Bentuk ini yang dipetakan langsung ke tabel ``transcripts``
    (TECH_SPEC §3): ``words`` JSONB, ``speakers`` JSONB, ``full_text``,
    ``model_used``.
    """

    language: str
    full_text: str
    words: list[TranscriptWord]
    #: Hasil diarisasi. MVP boleh kosong (list kosong) — lihat §5.2 catatan.
    speakers: list[dict[str, Any]] = field(default_factory=list)
    #: Nilai yang masuk ke kolom `transcripts.model_used`, mis. "small/int8".
    model_used: str = ""
    duration_s: float | None = None


@runtime_checkable
class Transcriber(Protocol):
    """Kontrak STT (TECH_SPEC §5.1).

    Implementasi WAJIB sinkron (bukan async): faster-whisper CPU-bound dan
    dijalankan di dalam Celery prefork worker, bukan di event loop FastAPI.
    """

    def transcribe(self, audio_path: str, language: str | None) -> TranscriptResult:
        """Transkripsi satu file audio menjadi :class:`TranscriptResult`.

        Args:
            audio_path: path absolut media (sudah dinormalisasi ke wav/mp3 mono).
            language: kode ISO-639-1, atau ``None`` untuk auto-detect.

        Returns:
            TranscriptResult berisi kata-kata bertimestamp.
        """
        ...


def check_canceled(job_id: str | None) -> None:
    """Lempar :class:`JobCanceled` bila job sudah dibatalkan/dihapus.

    Dipakai di dalam jalur transkripsi yang panjang: satu panggilan
    ``transcribe`` bisa berjalan puluhan menit, sedangkan pembatalan hanya
    terlihat di titik periksa ``emit`` bila tidak diperiksa di sini.
    Toleran terhadap DB yang sesaat tidak terbaca (lihat ``is_canceled``):
    kegagalan infrastruktur tidak boleh membatalkan pekerjaan pengguna.
    """
    from clipper_shared.worker_events import JobCanceled, is_canceled

    if job_id is not None and is_canceled(job_id):
        raise JobCanceled(job_id)


class FasterWhisperLocal(Transcriber):
    """Implementasi default MVP — ``faster-whisper`` CPU int8 (TECH_SPEC §0.1).

    Default model = ``small`` int8 (≈0.5–0.9× realtime pada 4 vCPU). Model
    di-cache di volume persisten (TECH_SPEC §4.4 butir 5) supaya tidak diunduh
    ulang tiap deploy.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        beam_size: int = 1,
        vad: bool = False,
        job_id: str | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        # `beam_size=1` (greedy) sebagai bawaan: pada CPU, beam search 5x lebih
        # lambat dengan peningkatan akurasi yang kecil untuk percakapan. Dinaikkan
        # hanya bila kualitas memang kurang.
        self.beam_size = beam_size
        # VAD nonaktif secara bawaan — lihat penjelasan di transcribe().
        self.vad = vad
        #: Job yang sedang ditranskripsi; dipakai untuk memutus antar segmen.
        self.job_id = job_id
        self._model: Any | None = None  # lazy, ctranslate2 WhisperModel

    def _load_model(self) -> Any:
        """Muat model sekali per proses (mahal: ratusan MB)."""
        if self._model is None:
            # Import lazy: container A tidak boleh butuh ctranslate2.
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root,
            )
        return self._model

    def transcribe(self, audio_path: str, language: str | None) -> TranscriptResult:
        """Jalankan Whisper lokal dan konversi segmen+kata ke TranscriptResult.

        **Tentang VAD.** ``vad_filter`` dibiarkan **nonaktif secara bawaan**.
        Pengujian nyata pada video uji menunjukkan filter itu membuang SELURUH
        audio: hasilnya nol segmen, dan ``info.language`` tetap melaporkan
        deteksi bahasa dengan keyakinan rendah — sehingga gejalanya tampak
        seperti "Whisper gagal" padahal audionya sengaja dibuang sebelum
        diproses. Karena itu ia hanya diaktifkan bila ``vad=True`` diminta
        eksplisit.

        ``word_timestamps=True`` wajib: efek karaoke pada subtitle dibangun dari
        waktu per kata (TECH_SPEC §5.2.1), dan tanpanya tahap render tidak punya
        data untuk menganimasikan sorotan.
        """
        import time

        started = time.monotonic()
        model = self._load_model()

        segments, info = model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            vad_filter=self.vad,
            beam_size=self.beam_size,
        )

        words: list[TranscriptWord] = []
        texts: list[str] = []
        speakers: list[dict[str, Any]] = []

        for segment in segments:
            # Generator segmen Whisper bersifat malas: satu segmen memakan
            # waktu nyata yang sama dengan durasinya. Memeriksa di sini membuat
            # pembatalan berhenti dalam hitungan detik, bukan setelah seluruh
            # rekaman selesai ditranskripsi.
            check_canceled(self.job_id)
            text = (segment.text or "").strip()
            if not text:
                continue
            texts.append(text)
            speakers.append(
                {
                    "start_s": float(segment.start),
                    "end_s": float(segment.end),
                    # Diarisasi ditunda (TECH_SPEC §5.3); label "SPEAKER_00"
                    # dipakai agar bentuk datanya sudah siap saat fitur itu ada,
                    # tanpa mengklaim pemisahan pembicara yang belum dilakukan.
                    "speaker": "SPEAKER_00",
                }
            )
            for word in segment.words or []:
                token = (word.word or "").strip()
                if not token:
                    continue
                words.append(
                    TranscriptWord(
                        # Nama field adalah `word`, bukan `text` — mengikuti
                        # kontrak TranscriptWord. Kesalahan di sini muncul
                        # sebagai TypeError saat task berjalan, setelah video
                        # terunduh dan audio terekstrak.
                        word=token,
                        start_s=float(word.start),
                        end_s=float(word.end),
                        probability=getattr(word, "probability", None),
                    )
                )

        duration = time.monotonic() - started

        return TranscriptResult(
            language=str(getattr(info, "language", "") or language or ""),
            words=words,
            full_text=" ".join(texts),
            model_used=f"faster-whisper:{self.model_size}",
            speakers=speakers,
            duration_s=duration,
        )


class RemoteWhisperAPI(Transcriber):
    """Cadangan berbasis API (OpenAI-compatible) saat ``STT_BACKEND=remote``.

    Dipakai bila throughput CPU tidak cukup. Kontrak hasilnya identik dengan
    implementasi lokal supaya pemanggil tidak perlu tahu backend mana yang aktif
    (TECH_SPEC §5.1).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "whisper-1",
        timeout_s: float = 600.0,
        job_id: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_s = timeout_s
        #: Job yang sedang ditranskripsi; diperiksa di sekitar unggahan audio.
        self.job_id = job_id

    def transcribe(self, audio_path: str, language: str | None) -> TranscriptResult:
        """Kirim audio ke API dan normalisasi respons ke TranscriptResult.

        Memakai ``response_format="verbose_json"`` dengan
        ``timestamp_granularities=["word"]`` karena hanya format itu yang
        memberi timestamp per kata — sama seperti jalur lokal, sehingga tahap
        hilir tidak perlu tahu backend mana yang dipakai.

        Raises:
            RuntimeError: bila API tidak mengembalikan data yang dapat dipakai.
                Kesalahannya sengaja informatif karena ini jalur cadangan: bila
                ia juga gagal, pengguna perlu tahu harus beralih ke apa.
        """
        import time

        import httpx

        # Sebelum mengirim: jangan unggah audio besar untuk job yang sudah
        # dibatalkan. (Pembatalan di TENGAH panggilan jaringan menunggu
        # timeout HTTP; itu batas yang sama dengan API eksternal mana pun.)
        check_canceled(self.job_id)

        started = time.monotonic()
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")

        with open(audio_path, "rb") as handle:
            files = {"file": (audio_path.rsplit("/", 1)[-1], handle, "audio/wav")}
            data = {
                "model": self.model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            }
            if language:
                data["language"] = language

            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(
                    f"{base}/audio/transcriptions",
                    headers={"authorization": f"Bearer {self.api_key}"},
                    files=files,
                    data=data,
                )

        if response.status_code >= 400:
            raise RuntimeError(
                f"STT API menolak permintaan (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )

        payload = response.json()
        raw_words = payload.get("words") or []
        raw_segments = payload.get("segments") or []

        words = [
            TranscriptWord(
                word=str(item.get("word", "")).strip(),
                start_s=float(item.get("start", 0.0)),
                end_s=float(item.get("end", 0.0)),
            )
            for item in raw_words
            if str(item.get("word", "")).strip()
        ]

        if not words:
            # Sebagian penyedia mengabaikan permintaan timestamp per kata.
            # Membagi rata durasi segmen adalah perkiraan yang jelas lebih baik
            # daripada mengembalikan hasil kosong yang menggagalkan pipeline.
            for segment in raw_segments:
                pieces = str(segment.get("text", "")).split()
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", start))
                if not pieces or end <= start:
                    continue
                span = (end - start) / len(pieces)
                words.extend(
                    TranscriptWord(
                        word=piece,
                        start_s=start + index * span,
                        end_s=start + (index + 1) * span,
                    )
                    for index, piece in enumerate(pieces)
                )

        full_text = str(payload.get("text") or "").strip()
        if not full_text and words:
            # Field-nya `word` (lihat TranscriptWord), bukan `text`. Memakai
            # nama yang salah tidak memunculkan error saat impor — baru meledak
            # ketika jalur ini benar-benar dijalankan.
            full_text = " ".join(item.word for item in words)

        if not words and not full_text:
            raise RuntimeError("STT API mengembalikan respons tanpa teks.")

        return TranscriptResult(
            language=str(payload.get("language") or language or ""),
            words=words,
            full_text=full_text,
            model_used=f"remote:{self.model}",
            speakers=[
                {
                    "start_s": float(segment.get("start", 0.0)),
                    "end_s": float(segment.get("end", 0.0)),
                    "speaker": "SPEAKER_00",
                }
                for segment in raw_segments
            ],
            duration_s=time.monotonic() - started,
        )


def get_transcriber(
    backend: str,
    *,
    model_size: str = "small",
    compute_type: str = "int8",
    download_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    job_id: str | None = None,
) -> Transcriber:
    """Factory: pilih implementasi berdasarkan env ``STT_BACKEND``.

    Args:
        backend: ``"local"`` (default MVP) atau ``"remote"``.
        model_size: ukuran model faster-whisper (dipakai saat backend lokal).
        compute_type: ``int8`` untuk CPU (TECH_SPEC §0.1).
        download_root: direktori cache model (volume persisten, §4.4).
        api_key: kredensial API Whisper (wajib saat backend remote).
        base_url: override endpoint OpenAI-compatible.

    Raises:
        ValueError: backend tidak dikenal, atau kredensial remote tidak lengkap.
    """
    normalized = backend.strip().lower()
    if normalized == "local":
        return FasterWhisperLocal(
            model_size=model_size,
            compute_type=compute_type,
            download_root=download_root,
            job_id=job_id,
        )
    if normalized == "remote":
        if not api_key:
            raise ValueError("STT_BACKEND=remote membutuhkan WHISPER_API_KEY")
        return RemoteWhisperAPI(api_key=api_key, base_url=base_url, job_id=job_id)
    raise ValueError(f"STT_BACKEND tidak dikenal: {backend!r} (harus 'local' atau 'remote')")
