"""Keamanan: enkripsi token OAuth (AES-256-GCM).

**Modul ini kini hanya re-export.** Implementasinya pindah ke
:mod:`clipper_shared.security` karena worker-light membutuhkannya juga, dan
``clipper_shared`` adalah satu-satunya paket yang di-distribusikan ke semua
container.

Sebelumnya worker-light mengimpor ``from app.core.security import TokenCipher``
— impor itu selalu gagal di jalur produksi (``apps/api`` tidak ada di
``PYTHONPATH`` worker), gagalnya ditelan ``except`` yang lebar, dan kunci API
penyedia AI menjadi kosong. Gejalanya muncul jauh di hilir sebagai
"penyedia memerlukan API key" — pesan yang menyesatkan dan menghabiskan waktu.

Re-export ini dipertahankan agar pemanggil lama (``app.api.v1.ai``, test suite)
tidak perlu diubah sekaligus, dan agar satu sumber kebenaran tetap terjaga:
tidak ada salinan implementasi kedua yang bisa menyimpang.
"""

from __future__ import annotations

from clipper_shared.security import (
    AES_256_KEY_BYTES,
    GCM_NONCE_BYTES,
    AESGCMKeyError,
    EncryptedBlob,
    TokenCipher,
    TokenDecryptionError,
    generate_key_b64,
)

__all__ = [
    "AES_256_KEY_BYTES",
    "GCM_NONCE_BYTES",
    "AESGCMKeyError",
    "EncryptedBlob",
    "TokenCipher",
    "TokenDecryptionError",
    "generate_key_b64",
]
