"""Keamanan: enkripsi token OAuth (AES-256-GCM).

TECH_SPEC §3 (catatan keamanan token): ``social_accounts.encrypted_token``
disimpan sebagai **AES-256-GCM via `cryptography`, key dari env/KMS — bukan
Fernet tanpa AAD**.

Kenapa GCM dan bukan Fernet:
* Fernet membungkus AES-CBC + HMAC tanpa konsep AAD. Tanpa AAD, ciphertext
  tidak terikat ke konteksnya: token milik user A bisa dipindah ke baris user B
  (atau dari kolom ``encrypted_token`` ke ``refresh_token``) dan tetap
  berhasil didekripsi.
* GCM adalah AEAD: satu operasi yang sama menghasilkan kerahasiaan + integritas,
  dan ``associated_data`` mengikat ciphertext ke (user_id, platform, kolom).
  Setiap perubahan konteks → ``InvalidTag``.

**Kenapa modul ini pindah ke `clipper_shared`.** Sebelumnya ia tinggal di
``apps/api/app/core/security.py``, dan worker-light mengimpornya dengan
``from app.core.security import TokenCipher``. Impor itu selalu gagal di
worker karena ``apps/api`` tidak ada di ``PYTHONPATH`` jalur produksi — gagal
di dalam ``except`` yang lebar, sehingga kunci API penyedia AI menjadi kosong
dan skoring mati dengan pesan menyesatkan "penyedia memerlukan API key",
padahal kuncinya ada di database. ``clipper_shared`` adalah satu-satunya paket
yang di-distribusikan ke SEMUA container (lihat ``[tool.hatch.build.targets.wheel]``),
jadi di sinilah kode yang dipakai bersama harus tinggal.

**JWT dan hash password sudah dihapus.** Autentikasi tidak lagi dipakai (lihat
``app/core/identity.py``); menyimpan fungsi pembuat token hanya menyisakan kode
yang tidak pernah diuji dan bisa salah dipakai kembali.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "AES_256_KEY_BYTES",
    "GCM_NONCE_BYTES",
    "AESGCMKeyError",
    "EncryptedBlob",
    "TokenCipher",
    "TokenDecryptionError",
    "generate_key_b64",
]

#: Panjang key AES-256 dalam byte.
AES_256_KEY_BYTES: Final[int] = 32
#: Panjang nonce GCM yang direkomendasikan (96 bit).
GCM_NONCE_BYTES: Final[int] = 12


class AESGCMKeyError(ValueError):
    """Key enkripsi tidak ada atau panjangnya bukan 32 byte."""


class TokenDecryptionError(Exception):
    """Ciphertext gagal diautentikasi: dirusak, dipindah, atau key salah.

    Sengaja tidak membedakan ketiga penyebab itu — membedakannya akan membocorkan
    informasi ke penyerang.
    """


@dataclass(frozen=True, slots=True)
class EncryptedBlob:
    """Ciphertext GCM beserta nonce-nya, siap disimpan sebagai satu string."""

    nonce: bytes
    ciphertext: bytes

    def to_b64(self) -> str:
        """Encode ke ``base64(nonce || ciphertext)`` untuk kolom TEXT."""
        return base64.b64encode(self.nonce + self.ciphertext).decode("ascii")

    @classmethod
    def from_b64(cls, value: str) -> EncryptedBlob:
        """Decode dari string yang dihasilkan :meth:`to_b64`."""
        raw = base64.b64decode(value.encode("ascii"))
        if len(raw) <= GCM_NONCE_BYTES:
            raise TokenDecryptionError("Blob terenkripsi terlalu pendek / rusak")
        return cls(nonce=raw[:GCM_NONCE_BYTES], ciphertext=raw[GCM_NONCE_BYTES:])


class TokenCipher:
    """Helper AES-256-GCM untuk token OAuth pihak ketiga.

    Bentuk pakai::

        cipher = TokenCipher.from_b64_key(settings.TOKEN_ENCRYPTION_KEY)
        blob = cipher.encrypt(access_token, aad=cipher.aad_for(user_id, "tiktok"))
        # ... simpan blob.to_b64() di social_accounts.encrypted_token
        plaintext = cipher.decrypt(EncryptedBlob.from_b64(stored), aad=...)
    """

    def __init__(self, key: bytes, *, default_aad: bytes = b"") -> None:
        if len(key) != AES_256_KEY_BYTES:
            raise AESGCMKeyError(
                f"Key harus {AES_256_KEY_BYTES} byte untuk AES-256-GCM, dapat {len(key)} byte"
            )
        self._aead = AESGCM(key)
        self._default_aad = default_aad

    @classmethod
    def from_b64_key(cls, key_b64: str, *, default_aad: str = "") -> TokenCipher:
        """Bangun cipher dari key base64 (format yang dipakai env/KMS)."""
        if not key_b64.strip():
            raise AESGCMKeyError(
                "TOKEN_ENCRYPTION_KEY kosong. Jalankan start.cmd (membuatnya "
                "otomatis di .env) atau python -c "
                "'from clipper_shared.security import generate_key_b64; "
                "print(generate_key_b64())'"
            )
        try:
            raw = base64.b64decode(key_b64.encode("ascii"), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise AESGCMKeyError("TOKEN_ENCRYPTION_KEY bukan base64 yang sah") from exc
        return cls(raw, default_aad=default_aad.encode("utf-8"))

    @staticmethod
    def aad_for(user_id: str, platform: str, field: str = "encrypted_token") -> bytes:
        """Bangun AAD yang mengikat ciphertext ke konteksnya.

        Ini inti keamanan: token TikTok milik user X hanya bisa didekripsi saat
        dibaca sebagai token TikTok milik user X dari kolom ``encrypted_token``.
        """
        return f"clipper|{user_id}|{platform}|{field}".encode()

    def encrypt(self, plaintext: str, *, aad: bytes | None = None) -> EncryptedBlob:
        """Enkripsi dengan nonce acak baru setiap panggilan.

        Nonce TIDAK BOLEH dipakai ulang dengan key yang sama — pengulangan nonce
        pada GCM menghancurkan kerahasiaan sekaligus integritasnya.
        """
        nonce = os.urandom(GCM_NONCE_BYTES)
        associated = aad if aad is not None else self._default_aad
        ciphertext = self._aead.encrypt(nonce, plaintext.encode("utf-8"), associated)
        return EncryptedBlob(nonce=nonce, ciphertext=ciphertext)

    def decrypt(self, blob: EncryptedBlob, *, aad: bytes | None = None) -> str:
        """Dekripsi dan verifikasi tag GCM.

        Raises:
            TokenDecryptionError: tag tidak cocok (data dirusak, AAD berbeda,
                atau ciphertext berasal dari baris/kolom lain).
        """
        associated = aad if aad is not None else self._default_aad
        try:
            plaintext = self._aead.decrypt(blob.nonce, blob.ciphertext, associated)
        except InvalidTag as exc:
            raise TokenDecryptionError(
                "Autentikasi AES-GCM gagal: ciphertext dimodifikasi atau AAD tidak cocok"
            ) from exc
        return plaintext.decode("utf-8")


def generate_key_b64() -> str:
    """Hasilkan key AES-256 acak dalam base64 (untuk dipasang di env)."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")
