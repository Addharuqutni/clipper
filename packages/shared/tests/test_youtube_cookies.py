"""Test validator cookies YouTube.

Yang diuji bukan "apakah berkas terbaca", melainkan apakah berkas yang SALAH
ditolak dengan pesan yang bisa ditindaklanjuti pengguna — karena kegagalan di
sini muncul sebagai unduhan yt-dlp yang misterius gagal di kemudian hari.
"""

from __future__ import annotations

from pathlib import Path

from clipper_shared.youtube_cookies import (
    REQUIRED_COOKIES,
    CookieValidation,
    validate_cookie_content,
    validate_cookie_file,
)

#: Format Netscape cookies.txt: 7 kolom dipisah TAB.
_NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc123\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tdef456\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSSID\tghi789\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tAPISID\tjkl012\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tmno345\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\tpqr678\n"
)


class TestValidCookie:
    def test_full_cookie_set_accepted(self) -> None:
        result = validate_cookie_content(_NETSCAPE)
        assert result.ok
        assert result.has_login
        assert not result.missing
        assert result.error == ""

    def test_partial_auth_cookie_still_accepted(self) -> None:
        # YouTube mengubah set cookie yang dikeluarkan dari waktu ke waktu.
        # Menolak berkas yang sebenarnya berfungsi akan membingungkan pengguna.
        partial = ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
        result = validate_cookie_content(partial)
        assert result.ok
        assert result.found == ("SID",)
        assert result.missing  # dilaporkan sebagai peringatan, bukan penolakan

    def test_secure_prefixed_variant_accepted(self) -> None:
        secure = ".youtube.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-3PSID\tabc\n"
        result = validate_cookie_content(secure)
        assert result.ok
        assert "__Secure-3PSID" in result.found


class TestRejectedInput:
    def test_empty_content(self) -> None:
        assert not validate_cookie_content("").ok
        assert not validate_cookie_content("   \n  ").ok

    def test_non_netscape_format_rejected(self) -> None:
        # JSON diekspor beberapa ekstensi; formatnya beda dan tidak terbaca.
        result = validate_cookie_content('{"cookies": [{"name": "SID"}]}')
        assert not result.ok
        assert "Netscape" in result.error

    def test_cookie_file_without_youtube_auth_rejected(self) -> None:
        # Cookies untuk situs lain tidak berguna untuk yt-dlp.
        other_site = ".example.com\tTRUE\t/\tTRUE\t1893456000\tSESSIONID\tabc\n"
        result = validate_cookie_content(other_site)
        assert not result.ok
        assert "tidak ada cookie autentikasi" in result.error.lower()

    def test_error_message_names_required_cookies(self) -> None:
        # Pesan harus memberi tahu apa yang kurang, bukan sekadar "gagal".
        result = validate_cookie_content(".example.com\tTRUE\t/\tTRUE\t1\tX\t1\n")
        assert not result.ok
        assert any(name in result.error for name in REQUIRED_COOKIES)

    def test_oversized_file_rejected(self) -> None:
        huge = "x" * (600 * 1024)
        result = validate_cookie_content(huge)
        assert not result.ok
        assert "terlalu besar" in result.error

    def test_error_message_is_actionable_when_hints_missing(self) -> None:
        result = validate_cookie_content('{"cookies": []}')
        # Harus menyebut cara memperbaikinya, bukan hanya menyatakan salah.
        assert "ekspor" in result.error.lower() or "Netscape" in result.error


class TestFileValidation:
    def test_missing_file(self, tmp_path: Path) -> None:
        result = validate_cookie_file(tmp_path / "tidak-ada.txt")
        assert not result.ok
        assert "tidak ditemukan" in result.error

    def test_directory_rejected(self, tmp_path: Path) -> None:
        result = validate_cookie_file(tmp_path)
        assert not result.ok
        assert "bukan sebuah berkas" in result.error

    def test_valid_file(self, tmp_path: Path) -> None:
        target = tmp_path / "cookies.txt"
        target.write_text(_NETSCAPE, encoding="utf-8")
        assert validate_cookie_file(target).ok


class TestCookieValidationModel:
    def test_has_login_reflects_found(self) -> None:
        assert not CookieValidation(ok=True).has_login
        assert CookieValidation(ok=True, found=("SID",)).has_login
