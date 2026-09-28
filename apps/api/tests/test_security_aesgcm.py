"""Test enkripsi token OAuth — AES-256-GCM dengan AAD (TECH_SPEC §3).

Test inti: **tampering harus terdeteksi**. Ini alasan kami tidak memakai Fernet
tanpa AAD: dengan GCM, mengubah satu bit ciphertext, memindahkan token ke user
lain, atau mengganti nama kolom semuanya menghasilkan ``InvalidTag``.
"""

from __future__ import annotations

import base64

import pytest
from app.core.security import (
    AESGCMKeyError,
    EncryptedBlob,
    TokenCipher,
    TokenDecryptionError,
    generate_key_b64,
)


@pytest.fixture
def cipher() -> TokenCipher:
    """Cipher dengan key acak yang valid (32 byte base64)."""
    return TokenCipher.from_b64_key(generate_key_b64())


class TestRoundTrip:
    """Kasus bahagia: enkripsi lalu dekripsi mengembalikan plaintext."""

    def test_roundtrip_mengembalikan_plaintext(self, cipher: TokenCipher) -> None:
        aad = TokenCipher.aad_for("user-1", "tiktok")
        blob = cipher.encrypt("token-rahasia-abc", aad=aad)
        assert cipher.decrypt(blob, aad=aad) == "token-rahasia-abc"

    def test_ciphertext_tidak_mengandung_plaintext(self, cipher: TokenCipher) -> None:
        """Ciphertext tidak boleh memuat plaintext mentah."""
        blob = cipher.encrypt("SUPER-SECRET-VALUE")
        assert b"SUPER-SECRET-VALUE" not in blob.ciphertext
        assert "SUPER-SECRET-VALUE" not in blob.to_b64()

    def test_nonce_berbeda_setiap_enkripsi(self, cipher: TokenCipher) -> None:
        """Nonce tidak boleh dipakai ulang dengan key yang sama.

        Pengulangan nonce pada GCM menghancurkan kerahasiaan sekaligus
        integritas, jadi ini properti keamanan, bukan detail implementasi.
        """
        first = cipher.encrypt("sama")
        second = cipher.encrypt("sama")
        assert first.nonce != second.nonce
        assert first.ciphertext != second.ciphertext


class TestTamperDetection:
    """Inti deliverable: setiap modifikasi harus ditolak.

    ``TokenDecryptionError`` menyebut tiga sebab berbeda (data dirusak, AAD
    berbeda, ciphertext dari baris/kolom lain). Ketiganya diuji di sini:

    1. modifikasi ciphertext (termasuk tag),
    2. modifikasi nonce,
    3. modifikasi konteks/AAD.
    """

    def test_bit_flip_pada_setiap_byte_ciphertext_terdeteksi(self, cipher: TokenCipher) -> None:
        """Membalik bit di SETIAP posisi harus gagal — bukan hanya byte pertama.

        Menguji satu posisi saja tidak membuktikan apa pun: implementasi yang
        hanya memverifikasi prefix (atau yang menelan error di posisi tertentu)
        akan lolos. Ciphertext GCM = payload || tag(16 byte), jadi loop ini
        mencakup modifikasi payload DAN tag autentikasi.
        """
        aad = TokenCipher.aad_for("user-1", "tiktok")
        blob = cipher.encrypt("token-rahasia-yang-panjang", aad=aad)
        assert len(blob.ciphertext) >= 16  # payload + tag

        for position in range(len(blob.ciphertext)):
            tampered = bytearray(blob.ciphertext)
            tampered[position] ^= 0x01
            corrupted = EncryptedBlob(nonce=blob.nonce, ciphertext=bytes(tampered))

            with pytest.raises(TokenDecryptionError):
                cipher.decrypt(corrupted, aad=aad)

    def test_tag_autentikasi_16_byte_terakhir_terlindungi(self, cipher: TokenCipher) -> None:
        """Tag GCM (16 byte terakhir) harus gagal bila diubah.

        Bila tag tidak diverifikasi, penyerang bisa memalsukan plaintext tanpa
        mengetahui key — kegagalan paling berbahaya pada mode AEAD.
        """
        blob = cipher.encrypt("token-rahasia")
        assert len(blob.ciphertext) > 16

        for offset in range(16):
            tampered = bytearray(blob.ciphertext)
            tampered[len(blob.ciphertext) - 1 - offset] ^= 0x80
            with pytest.raises(TokenDecryptionError):
                cipher.decrypt(EncryptedBlob(nonce=blob.nonce, ciphertext=bytes(tampered)))

    def test_bit_flip_pada_setiap_byte_nonce_terdeteksi(self, cipher: TokenCipher) -> None:
        """Nonce terautentikasi; setiap posisinya harus divalidasi."""
        blob = cipher.encrypt("token-rahasia")

        for position in range(len(blob.nonce)):
            tampered = bytearray(blob.nonce)
            tampered[position] ^= 0x01
            with pytest.raises(TokenDecryptionError):
                cipher.decrypt(EncryptedBlob(nonce=bytes(tampered), ciphertext=blob.ciphertext))

    def test_pertukaran_dua_ciphertext_gagal(self, cipher: TokenCipher) -> None:
        """Menukar blok antar dua ciphertext harus merusak tag."""
        aad = TokenCipher.aad_for("user-1", "tiktok")
        first = cipher.encrypt("token-pertama", aad=aad)
        second = cipher.encrypt("token-kedua", aad=aad)

        # Ambil nonce dari first, ciphertext dari second.
        mixed = EncryptedBlob(nonce=first.nonce, ciphertext=second.ciphertext)
        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(mixed, aad=aad)

    def test_ciphertext_kosong_ditolak(self, cipher: TokenCipher) -> None:
        """Ciphertext tanpa payload+tag tidak boleh menghasilkan plaintext kosong."""
        blob = cipher.encrypt("token-rahasia")
        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(EncryptedBlob(nonce=blob.nonce, ciphertext=b""))

    def test_tag_yang_dipotong_terdeteksi(self, cipher: TokenCipher) -> None:
        """Memotong tag autentikasi (16 byte terakhir) harus gagal."""
        blob = cipher.encrypt("token-rahasia")
        truncated = EncryptedBlob(nonce=blob.nonce, ciphertext=blob.ciphertext[:-4])

        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(truncated)

    def test_tag_yang_ditambah_ditolak(self, cipher: TokenCipher) -> None:
        """Menambahkan byte ke ciphertext harus gagal (bukan diabaikan).

        Padding yang diam-diam diabaikan adalah cara klasik menyelundupkan data.
        """
        blob = cipher.encrypt("token-rahasia")
        padded = EncryptedBlob(nonce=blob.nonce, ciphertext=blob.ciphertext + b"\x00")

        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(padded)

    def test_aad_berbeda_terdeteksi(self, cipher: TokenCipher) -> None:
        """Ciphertext tidak bisa didekripsi dengan konteks (AAD) yang berbeda.

        Inilah perlindungan yang tidak dimiliki Fernet: token milik user A tidak
        bisa "dipindahkan" ke baris user B.
        """
        blob = cipher.encrypt("token-user-A", aad=TokenCipher.aad_for("user-A", "tiktok"))

        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(blob, aad=TokenCipher.aad_for("user-B", "tiktok"))

    def test_pindah_kolom_terdeteksi(self, cipher: TokenCipher) -> None:
        """Token tidak bisa dipindah dari encrypted_token ke refresh_token."""
        blob = cipher.encrypt(
            "access", aad=TokenCipher.aad_for("user-1", "tiktok", "encrypted_token")
        )

        with pytest.raises(TokenDecryptionError):
            cipher.decrypt(blob, aad=TokenCipher.aad_for("user-1", "tiktok", "refresh_token"))

    def test_key_berbeda_terdeteksi(self, cipher: TokenCipher) -> None:
        """Ciphertext dari key lain tidak bisa didekripsi."""
        blob = cipher.encrypt("token-rahasia")
        other = TokenCipher.from_b64_key(generate_key_b64())

        with pytest.raises(TokenDecryptionError):
            other.decrypt(blob)

    def test_blob_rusak_dari_string_b64_ditolak(self, cipher: TokenCipher) -> None:
        """String yang terlalu pendek ditolak sebelum menyentuh AESGCM."""
        with pytest.raises(TokenDecryptionError):
            EncryptedBlob.from_b64(base64.b64encode(b"pendek").decode())


class TestKeyValidation:
    """Key harus tepat 32 byte untuk AES-256."""

    def test_key_pendek_ditolak(self) -> None:
        with pytest.raises(AESGCMKeyError):
            TokenCipher(b"\x00" * 16)

    def test_key_kosong_ditolak(self) -> None:
        with pytest.raises(AESGCMKeyError):
            TokenCipher.from_b64_key("")

    def test_key_bukan_base64_ditolak(self) -> None:
        with pytest.raises(AESGCMKeyError):
            TokenCipher.from_b64_key("!!! bukan base64 !!!")

    def test_generate_key_b64_menghasilkan_32_byte(self) -> None:
        raw = base64.b64decode(generate_key_b64())
        assert len(raw) == 32
