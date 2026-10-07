"""Uji koneksi penyedia AI: resolusi konfigurasi dan satu permintaan kecil.

Dipisah dari :mod:`app.services.ai_settings` karena tujuannya berbeda:
``ai_settings`` menyimpan pengaturan, sedangkan modul ini MEMBUKTIKAN bahwa
konfigurasi bekerja dan menerjemahkan kegagalan menjadi saran yang bisa
ditindaklanjuti. Kunci tersimpan boleh dipakai (lihat
:func:`resolve_probe_config`), tetapi tidak pernah dicatat ke log dan tidak
pernah disimpan ulang.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from clipper_shared.ai_provider import AIProviderConfig, ProviderPreset
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_settings

#: Uji koneksi harus cepat; model besar bisa lambat, jadi batasnya lebih longgar
#: daripada panggilan biasa tetapi tetap mencegah permintaan menggantung.
TEST_TIMEOUT_S = 45.0

#: Anggaran token jawaban uji koneksi. **Jangan diturunkan ke belasan token.**
#: Model penalaran (``reasoning_content``) menghabiskan anggaran ini lebih dulu
#: untuk berpikir sebelum menulis ``content``. Dengan anggaran sekecil 16 token
#: jawaban habis di tengah penalaran, ``content`` tetap ``null``, dan uji
#: koneksi gagal PALSU dengan "tanpa isi yang dapat dibaca" padahal endpoint
#: sehat. Nilai ini cukup untuk penalaran singkat sekaligus tetap murah.
PROBE_MAX_TOKENS = 1024

#: Panjang cuplikan jawaban yang ditampilkan di UI.
_SAMPLE_CHARS = 200

#: Prompt bawaan uji koneksi. Sekaligus membuktikan endpoint hidup DAN
#: menanyakan jendela konteks model, supaya pengguna tidak perlu menebak ukuran
#: konteks secara manual. Jawabannya dipakai untuk mengisi kolom "Konteks model"
#: di setelan (lihat :func:`context_tokens_from_reply`).
PROBE_PROMPT = (
    "Jawab HANYA dengan satu angka tanpa penjelasan atau satuan tambahan: "
    "berapa ukuran jendela konteks (context window) model ini dalam token?"
)

#: Batas nilai konteks yang masuk akal — selaras dengan validasi setelan
#: (``ge=1024, le=100_000_000``). Nilai di luar rentang ini diabaikan agar
#: salah tulis atau halusinasi model tidak tersimpan sebagai konteks.
_MIN_CONTEXT_TOKENS = 1_024
_MAX_CONTEXT_TOKENS = 100_000_000

#: Angka pertama pada jawaban, dengan sufiks ``k``/``m`` opsional (mis. "128k").
_REPLY_NUMBER_RE = re.compile(r"(\d[\d.,]*)\s*([kKmM])?")


async def resolve_probe_config(
    db: AsyncSession,
    user_id: Any,
    *,
    preset: ProviderPreset,
    base_url: str | None,
    model: str | None,
    api_key: str,
    allow_private_host: bool,
) -> AIProviderConfig:
    """Konfigurasi untuk uji koneksi, dengan fallback ke kunci tersimpan.

    **Kunci tersimpan dipakai bila field kunci kosong.** Form pengaturan
    membiarkan field kunci kosong dengan placeholder "(tersimpan)" karena server
    tidak pernah mengembalikan nilainya. Tanpa pengambilan kunci tersimpan di
    sini, menekan "Uji koneksi" setelah menyimpan akan SELALU gagal dengan
    "memerlukan API key" — padahal kuncinya ada dan sudah tersimpan.

    Raises:
        BadRequestError: konfigurasi tidak sah (400, bukan 500).
    """
    override_fields = bool(base_url and base_url.strip()) or bool(model and model.strip())
    stored_key = ""
    if not api_key.strip() and not (override_fields or allow_private_host):
        row = await ai_settings.load_settings(db, user_id)
        # Hanya untuk preset yang sama dengan yang tersimpan: preset lain berarti
        # alamat lain, dan kunci tidak boleh dikirim ke sana.
        if row is not None and row.preset == preset.value:
            stored_key = ai_settings.decrypt_api_key(row)

    return ai_settings.resolve_configured_provider(
        preset=preset,
        base_url=base_url,
        model=model,
        api_key=api_key.strip() or stored_key,
        allow_private=allow_private_host,
    )


async def probe_provider(config: AIProviderConfig) -> dict[str, Any]:
    """Panggil endpoint chat completions dengan prompt minimal.

    Kesalahan dibedakan dengan sengaja: konfigurasi (pesan perbaikan), jaringan
    (saran periksa koneksi), dan penyedia (saran periksa key/nama model).
    """
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"

    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        # Rendah agar jawabannya dapat diprediksi dan murah.
        "temperature": 0.0,
        # Lihat PROBE_MAX_TOKENS: terlalu kecil membuat model penalaran
        # kehabisan anggaran sebelum menulis jawaban.
        "max_tokens": PROBE_MAX_TOKENS,
    }

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_S) as client:
            response = await client.post(config.chat_completions_url, headers=headers, json=body)
    except httpx.TimeoutException:
        return {
            "ok": False,
            "message": f"Penyedia tidak merespons dalam {TEST_TIMEOUT_S:.0f} detik.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "hint": "Periksa koneksi jaringan, atau coba model yang lebih kecil.",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"Tidak dapat menghubungi penyedia: {exc}",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "hint": "Pastikan URL dasar benar dan dapat dijangkau dari server ini.",
        }

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code >= 400:
        return {
            "ok": False,
            "message": f"Penyedia menolak permintaan (HTTP {response.status_code}).",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": hint_for_status(response.status_code),
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "message": "Penyedia membalas dengan format yang bukan JSON.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": (
                "URL mungkin menunjuk ke halaman web, bukan endpoint API. "
                "Pastikan diakhiri dengan /v1."
            ),
        }

    sample = extract_sample(data)
    if not sample:
        return {
            "ok": False,
            "message": "Penyedia membalas tanpa isi yang dapat dibaca.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": (
                "Balasan kosong. Bila model ini model penalaran, jawabannya "
                "mungkin habis terpakai untuk berpikir; coba model lain atau "
                "periksa format balasan penyedia."
            ),
        }

    # Jawaban yang TERPOTONG (``finish_reason: "length"``) tidak boleh dipakai
    # menebak konteks: angka yang terpotong separuh (mis. "10485" dari
    # "1048576") masih terlihat wajar. Cuplikannya tetap ditampilkan, tetapi
    # kolom konteks dibiarkan kosong agar tidak diisi nilai salah.
    context_tokens = None
    if not reply_was_truncated(data):
        context_tokens = context_tokens_from_reply(extract_answer(data))

    return {
        "ok": True,
        "message": "Koneksi berhasil.",
        "resolved_base_url": config.base_url,
        "resolved_model": config.model,
        "latency_ms": latency_ms,
        "sample": sample,
        "context_tokens": context_tokens,
    }


def hint_for_status(code: int) -> str:
    """Terjemahkan kode HTTP menjadi saran yang bisa ditindaklanjuti."""
    if code in {401, 403}:
        return "API key ditolak. Periksa kembali key-nya dan pastikan masih aktif."
    if code == 404:
        return "Endpoint atau nama model tidak ditemukan. Periksa URL dasar dan nama model."
    if code == 429:
        return "Kuota penyedia habis atau terlalu banyak permintaan. Coba lagi nanti."
    if code >= 500:
        return "Penyedia sedang bermasalah. Coba lagi, atau pilih penyedia lain."
    return "Periksa kembali konfigurasi penyedia."


def extract_answer(data: object) -> str:
    """Teks jawaban UTUH (tanpa pemotongan) dari respons bergaya OpenAI.

    Berbeda dari :func:`extract_sample` yang memotong untuk ditampilkan di UI,
    fungsi ini mengembalikan teks penuh supaya angka konteks di ujung jawaban
    tidak terpotong. ``reasoning_content`` **tidak** dipakai di sini: isinya
    penalaran bebas yang penuh angka, jadi tidak layak dijadikan sumber ukuran
    konteks. Bila penyedia hanya mengisi ``reasoning_content``, hasilnya kosong
    dan konteks dibiarkan tidak terbaca — lebih baik daripada angka salah.
    """
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    for key in ("output_text", "content", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""

def extract_sample(data: object) -> str:
    """Cuplikan teks dari respons bergaya OpenAI untuk ditampilkan di UI.

    Struktur respons berbeda antar penyedia; fungsi ini menelusuri bentuk yang
    umum dan mengembalikan string kosong bila tidak menemukan apa pun — daripada
    melempar ``KeyError`` yang akan muncul sebagai 500 tanpa penjelasan.

    ``reasoning_content`` dipakai sebagai upaya terakhir **hanya untuk
    cuplikan**. Model penalaran menaruh jawaban akhir di ``content``, tetapi
    sebagian penyedia/proxy hanya mengisi ``reasoning_content`` sehingga
    ``content`` tetap ``null`` — uji koneksi tetap layak dianggap berhasil.
    """
    answer = extract_answer(data)
    if answer:
        return answer[:_SAMPLE_CHARS]

    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    reasoning = message.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning.strip():
                        return reasoning.strip()[:_SAMPLE_CHARS]

    return ""

def reply_was_truncated(data: object) -> bool:
    """True bila penyedia memotong jawaban karena anggaran token habis.

    Jawaban terpotong berarti ``content`` mungkin hanya separuh — mis. angka
    konteks ``"1048576"`` menjadi ``"10485"``. Angka separuh itu masih terlihat
    wajar, jadi ia tidak boleh dipercaya sebagai ukuran konteks.
    """
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    if not isinstance(first, dict):
        return False
    return first.get("finish_reason") == "length"


def context_tokens_from_reply(reply: str) -> int | None:
    """Baca jendela konteks dari jawaban model, bila jawabannya berupa angka.

    Prompt uji koneksi meminta model menyebut ukuran konteksnya. Jawaban bebas
    seperti ``"128000"``, ``"128k token"``, atau ``"1.048.576"`` diterima;
    yang tidak memuat angka wajar dikembalikan ``None``.

    **Jawaban model bukan sumber tepercaya.** Model kerap menyebut angka yang
    tidak akurat, jadi nilai ini hanya PREFILL kolom di UI — pengguna tetap
    meninjaunya, dan deteksi ``GET /models`` yang deterministik (lihat
    :func:`app.services.ai_settings.detect_context_tokens`) tetap menjadi jaring
    pengaman saat kolom dibiarkan kosong.
    """
    match = _REPLY_NUMBER_RE.search(reply)
    if match is None:
        return None
    digits = match.group(1).replace(".", "").replace(",", "")
    if not digits.isdigit():
        return None
    value = int(digits)
    suffix = match.group(2)
    if suffix:
        value *= 1_000 if suffix.lower() == "k" else 1_000_000
    if _MIN_CONTEXT_TOKENS <= value <= _MAX_CONTEXT_TOKENS:
        return value
    return None
