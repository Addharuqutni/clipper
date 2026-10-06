"""Akses disk dan basis data untuk worker-light.

Menyatukan semua sentuhan ke SQLite, penyimpanan lokal, dan FFmpeg di satu modul
agar task tetap terbaca sebagai alur kerja, bukan sebagai kode infrastruktur.

**Catatan keamanan cookies.** Cookies YouTube disimpan terenkripsi di disk,
didekripsi hanya selama dipakai,
dan ditulis ke berkas sementara di dalam ruang kerja job. Pemanggil wajib
menghapus ruang kerja itu setelah selesai — ``ingest_media`` melakukannya lewat
``shutil.rmtree`` di blok ``finally``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from clipper_shared import storage as layout
from clipper_shared.ai_provider import ProviderConfig
from clipper_shared.db import get_db_connection, utc_now
from clipper_shared.processes import run_process

logger = logging.getLogger(__name__)

#: AAD cookies YouTube. HARUS sama dengan ``app.api.v1.youtube.COOKIES_AAD``.
COOKIES_AAD = b"clipper.source_media.youtube_cookies"


def fetch_youtube_cookies(job_id: str, work_dir: Path) -> Path | None:
    """Dekripsi cookies YouTube pengguna ke berkas sementara di ruang kerja.

    Cookies diunggah lewat halaman YouTube dan disimpan terenkripsi di
    :func:`clipper_shared.storage.youtube_cookies_path`.

    Returns:
        Jalur berkas cookies, atau ``None`` bila tidak ada. ``None`` bukan
        error: banyak video dapat diunduh tanpa cookies.
    """
    from clipper_shared.security import EncryptedBlob, TokenCipher
    from clipper_shared.youtube_cookies import validate_cookie_content

    stored = layout.youtube_cookies_path()
    if not stored.is_file():
        return None
    key_b64 = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not key_b64:
        logger.warning("TOKEN_ENCRYPTION_KEY kosong; cookies YouTube tidak dapat dibaca (job %s)", job_id)
        return None
    try:
        plaintext = TokenCipher.from_b64_key(key_b64).decrypt(
            EncryptedBlob.from_b64(stored.read_text(encoding="ascii")), aad=COOKIES_AAD
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gagal mendekripsi cookies YouTube (job %s): %s", job_id, exc)
        return None

    validation = validate_cookie_content(plaintext)
    if not validation.ok:
        logger.warning("Cookies YouTube tersimpan tidak valid: %s", validation.error)
        return None

    path = work_dir / "cookies.txt"
    path.write_text(plaintext, encoding="utf-8")
    return path


def _run_ffmpeg(args: list[str], what: str, job_id: str) -> None:
    """Jalankan FFmpeg sekali jalan, terdaftar untuk pembatalan job.

    Raises:
        RuntimeError: FFmpeg keluar bukan nol.
        JobCanceled: job dibatalkan; proses sudah dimatikan.
    """
    result = run_process(
        [layout.binary("ffmpeg"), "-y", "-nostdin", "-loglevel", "error", *args],
        job_id=job_id,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Gagal {what}: {result.stderr[-400:]}")


def extract_audio(media_path: Path, work_dir: Path, *, job_id: str) -> Path:
    """Ekstrak audio menjadi WAV mono 16 kHz untuk Whisper lokal.

    Whisper dilatih pada 16 kHz mono; memberinya 48 kHz stereo hanya menambah
    resampling internal tanpa meningkatkan akurasi.
    """
    target = work_dir / "audio.wav"
    _run_ffmpeg(
        ["-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        "mengekstrak audio",
        job_id,
    )
    return target


def extract_audio_chunks(
    media_path: Path, work_dir: Path, *, job_id: str, chunk_s: int = 600
) -> list[tuple[Path, float]]:
    """Audio untuk STT remote: MP3 32 kbps, dipotong per ``chunk_s`` detik.

    API Whisper OpenAI menolak berkas > 25 MB. WAV 16 kHz sudah menembus batas
    itu di menit ke-13; MP3 32 kbps berukuran ~0,24 MB/menit sehingga potongan
    10 menit (~2,4 MB) selalu aman.

    Returns:
        Daftar ``(jalur, offset_detik)`` terurut.
    """
    pattern = work_dir / "chunk-%03d.mp3"
    _run_ffmpeg(
        [
            "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "32k",
            "-f", "segment", "-segment_time", str(chunk_s), "-reset_timestamps", "1",
            str(pattern),
        ],
        "memotong audio",
        job_id,
    )
    chunks = sorted(work_dir.glob("chunk-*.mp3"))
    return [(path, float(index * chunk_s)) for index, path in enumerate(chunks)]


def load_transcript(job_id: str) -> dict[str, Any] | None:
    """Ambil transkrip job beserta daftar katanya."""
    from clipper_shared import transcript as transcript_mod
    return transcript_mod.load_transcript(job_id)


def load_provider_config(job_id: str) -> ProviderConfig:
    """Ambil konfigurasi penyedia AI yang berlaku untuk sebuah job."""
    from clipper_shared.ai_provider import ProviderPreset, env_provider_config

    row: tuple[Any, ...] | None = None
    try:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.preset, s.base_url, s.model, s.api_key_encrypted,
                       s.allow_private_host, s.default_direction, j.user_id,
                       s.context_tokens
                  FROM ai_provider_settings s
                  JOIN jobs j ON j.user_id = s.user_id
                 WHERE j.id = %s
                """,
                (job_id,),
            )
            row = cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        # Env masih bisa menyediakan cadangan; jangan gagalkan job di sini.
        logger.warning("Gagal membaca pengaturan penyedia AI untuk job %s: %s", job_id, exc)

    if row is not None:
        return {
            "preset": row[0] or ProviderPreset.CUSTOM.value,
            "base_url": row[1] or "",
            "model": row[2] or "",
            "api_key": _decrypt_provider_key(row[3], row[6]) if row[3] else "",
            "allow_private_host": bool(row[4]),
            "default_direction": row[5] or "",
            "context_tokens": row[7],
        }
    return env_provider_config()


def _decrypt_provider_key(ciphertext: str, user_id: Any) -> str:
    """Dekripsi kunci API penyedia dengan AAD yang sama seperti saat menulis."""
    try:
        from clipper_shared.security import EncryptedBlob, TokenCipher

        key_b64 = os.getenv("TOKEN_ENCRYPTION_KEY", "")
        if not key_b64:
            return ""
        aad = TokenCipher.aad_for(str(user_id), "ai_provider", "api_key_encrypted")
        return TokenCipher.from_b64_key(key_b64).decrypt(EncryptedBlob.from_b64(ciphertext), aad=aad)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Gagal mendekripsi kunci API penyedia (user %s): %s. "
            "Kunci perlu diisi ulang lewat halaman Setelan.",
            user_id,
            exc,
        )
        return ""


def replace_segments(*, job_id: str, candidates: list[Any]) -> list[str]:
    """Ganti segmen job dengan hasil skoring. Mengembalikan ID segmen yang disimpan."""
    inserted: list[str] = []
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM segments WHERE job_id = %s", (job_id,))
        for candidate in candidates:
            seg_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO segments
                    (id, job_id, start_s, end_s, score, label,
                     hook_score, completeness, emotional_arc, reason, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'proposed')
                """,
                (
                    seg_id, job_id, candidate.start_s, candidate.end_s, candidate.score,
                    candidate.label, candidate.hook_score, candidate.completeness,
                    candidate.emotional_arc, candidate.reason,
                ),
            )
            inserted.append(seg_id)
    return inserted


def queue_renders(segment_ids: list[str], kind: str = "final") -> None:
    """Buat baris render ``queued`` agar UI langsung menampilkan status merender."""
    now = utc_now()
    with get_db_connection() as connection, connection.cursor() as cursor:
        for seg_id in segment_ids:
            cursor.execute(
                """
                INSERT INTO renders (id, segment_id, kind, crop_mode, status, created_at)
                VALUES (%s, %s, %s, 'face_track', 'queued', %s)
                """,
                (str(uuid.uuid4()), seg_id, kind, now),
            )


def probe_media(path: Path, *, job_id: str) -> dict[str, Any]:
    """Baca metadata media dengan ffprobe.

    Lebar/tinggi yang dikembalikan adalah ukuran **tampilan**: video HP sering
    disimpan 1920x1080 dengan metadata rotasi 90°, dan FFmpeg memutarnya saat
    decode. Tanpa penukaran ini, semua perhitungan crop memakai orientasi salah.
    """
    result = run_process(
        [layout.binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        job_id=job_id,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe gagal: {result.stderr[-400:]}")

    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("Berkas tidak memuat trek video.")

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if stream_rotation(video) % 180 == 90:
        width, height = height, width
    return {
        "duration_s": float(data.get("format", {}).get("duration") or video.get("duration") or 0.0),
        "width": width,
        "height": height,
        "codec": str(video.get("codec_name") or ""),
        "size_bytes": int(data.get("format", {}).get("size") or path.stat().st_size),
    }


def stream_rotation(video_stream: dict[str, Any]) -> int:
    """Rotasi tampilan (0/90/180/270) dari tag lama ``rotate`` atau side data."""
    raw: Any = (video_stream.get("tags") or {}).get("rotate")
    for side in video_stream.get("side_data_list") or []:
        if "rotation" in side:
            raw = side["rotation"]
    try:
        return int(round(float(raw or 0))) % 360
    except (TypeError, ValueError):
        return 0


def record_source_media(
    *,
    job_id: str,
    r2_key: str,
    size_bytes: int,
    duration_s: float,
    width: int,
    height: int,
    codec: str,
    language: str | None,
    transcript_source: str | None,
    video_title: str | None = None,
) -> None:
    """Simpan metadata media (baris dibuat saat unggahan atau di sini untuk YouTube).

    ``video_title`` diisi hanya bila judul BARU diketahui worker — yaitu
    ``YoutubeMetadata.title`` pada job YouTube; nilai ``None`` tidak menimpa
    judul yang sudah ada, sehingga ingest ulang job unggahan tidak menghapus
    nama berkas yang dipasang API.
    Judul yang berubah belakangan tidak memindahkan folder job: namanya
    sudah terekam di ``r2_key``.

    ``expires_at`` sengaja TIDAK diisi di sini: TECH_SPEC §3 menghapus media
    48 jam setelah **render terakhir** selesai, bukan setelah ingest. Nilainya
    ditetapkan worker-render.
    """
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO source_media
                (id, job_id, r2_key, size_bytes, duration_s, width, height,
                 codec, language, transcript_source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE
               SET r2_key = EXCLUDED.r2_key,
                   size_bytes = EXCLUDED.size_bytes,
                   duration_s = EXCLUDED.duration_s,
                   width = EXCLUDED.width,
                   height = EXCLUDED.height,
                   codec = EXCLUDED.codec,
                   language = EXCLUDED.language,
                   transcript_source = EXCLUDED.transcript_source
            """,
            (
                str(uuid.uuid4()), job_id, r2_key, size_bytes, duration_s, width, height, codec,
                language, transcript_source, utc_now(),
            ),
        )
        if video_title:
            cursor.execute(
                "UPDATE jobs SET video_title = %s, updated_at = %s WHERE id = %s",
                (video_title, utc_now(), job_id),
            )


def save_transcript(
    *,
    job_id: str,
    language: str | None,
    words: list[dict[str, object]],
    full_text: str,
    model_used: str,
) -> None:
    """Simpan transkrip (kata bertimestamp) untuk sebuah job."""
    from clipper_shared import transcript as transcript_mod
    transcript_mod.save_transcript(
        job_id=job_id,
        language=language,
        words=words,
        full_text=full_text,
        model_used=model_used,
    )


def save_segments(*, job_id: str, words: list[dict[str, object]]) -> None:
    """Simpan potongan 30–60 detik sebagai kandidat awal sebelum skoring.

    Potongan ini tampil di UI selama skoring berjalan dan tetap ada bila skoring
    gagal total.
    """
    from worker_light.scoring_client import chunk_words

    chunks = chunk_words(list(words))
    if not chunks:
        return
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM segments WHERE job_id = %s", (job_id,))
        for index, (seg_start, seg_end) in enumerate(chunks):
            cursor.execute(
                """
                INSERT INTO segments
                    (id, job_id, start_s, end_s, score, label,
                     hook_score, completeness, emotional_arc, reason, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'proposed')
                """,
                (
                    str(uuid.uuid4()), job_id, seg_start, seg_end, 0.5, "belum dinilai",
                    0.5, 0.5, 0.5, f"Segmentasi otomatis #{index + 1}",
                ),
            )
