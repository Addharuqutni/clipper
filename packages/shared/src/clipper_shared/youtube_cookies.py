"""Validasi berkas cookies YouTube untuk yt-dlp.

Pola diambil dari repo referensi `jipraks/yt-short-clipper` (``cookies.py``).

**Kenapa ini perlu:** tanpa cookies, ``yt-dlp`` mudah ditolak YouTube untuk
video yang memerlukan login, video dengan pembatasan usia, atau saat IP server
sudah ditandai. Risiko ini sudah tercatat di PRD §7 ("Pemblokiran IP"). Cookies
dari sesi browser pengguna adalah cara paling andal melewatinya.

**Peringatan keamanan yang mengikat:** cookies YouTube setara dengan kredensial
sesi penuh — siapa pun yang memegangnya dapat mengakses akun. Karena itu modul
ini hanya MEMVALIDASI, dan pemanggil wajib menyimpan isinya terenkripsi
(AES-256-GCM, lihat TECH_SPEC §3) serta menghapusnya setelah job selesai.
Cookies milik pengguna TIDAK BOLEH dipakai lintas pengguna.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Cookies yang menandakan sesi login YouTube yang sah. ``SID`` dan
#: ``LOGIN_INFO`` adalah yang paling menentukan; sisanya penguat.
REQUIRED_COOKIES = ("SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO")

#: YouTube juga mengeluarkan varian berprefiks ini untuk konteks aman.
SECURE_COOKIE_PREFIXES = ("__Secure-1P", "__Secure-3P")

#: Batas ukuran berkas. cookies.txt normalnya hanya beberapa KB; berkas jauh
#: lebih besar dari ini menandakan unggahan yang salah.
MAX_COOKIE_FILE_BYTES = 512 * 1024

#: Format Netscape cookies.txt: 7 kolom dipisah TAB.
_COOKIE_LINE = re.compile(r"^[^\t#]+\t[^\t]*\t[^\t]*\t[^\t]*\t[^\t]*\t[^\t]*\t[^\t]*$")


@dataclass(frozen=True, slots=True)
class CookieValidation:
    """Hasil validasi cookies."""

    ok: bool
    found: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    error: str = ""

    @property
    def has_login(self) -> bool:
        """True bila minimal satu cookie identitas ditemukan.

        Kami sengaja TIDAK mewajibkan keenam cookie: YouTube secara berkala
        mengubah set yang dikeluarkan, dan menolak berkas yang sebenarnya
        berfungsi akan membuat pengguna bingung.
        """
        return bool(self.found)


def _cookie_names(content: str) -> set[str]:
    """Kumpulkan nama cookie dari isi berkas format Netscape."""
    names: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        columns = stripped.split("\t")
        if len(columns) >= 7:
            names.add(columns[5].strip())
    return names


def _matches_required(name: str) -> str | None:
    """Cocokkan nama cookie ke daftar wajib, termasuk varian berprefiks."""
    if name in REQUIRED_COOKIES:
        return name
    for prefix in SECURE_COOKIE_PREFIXES:
        for required in REQUIRED_COOKIES:
            if name == f"{prefix}{required}":
                return f"{prefix}{required}"
    return None


def validate_cookie_content(content: str) -> CookieValidation:
    """Validasi isi cookies.txt tanpa menyentuh disk.

    Dipisah dari :func:`validate_cookie_file` supaya isi yang datang dari
    unggahan browser (belum tersimpan) bisa diperiksa lebih dulu — dan supaya
    isi cookie tidak perlu ditulis ke disk hanya untuk diperiksa.
    """
    if not content.strip():
        return CookieValidation(ok=False, error="Berkas cookies kosong.")

    if len(content.encode("utf-8", errors="ignore")) > MAX_COOKIE_FILE_BYTES:
        return CookieValidation(ok=False, error="Berkas cookies terlalu besar; periksa kembali berkasnya.")

    if "\t" not in content:
        return CookieValidation(
            ok=False,
            error=(
                "Format tidak dikenal. Berkas ini bukan cookies.txt format Netscape "
                "(kolom dipisah TAB). Ekspor ulang dengan ekstensi "
                "'Get cookies.txt LOCALLY'."
            ),
        )

    names = _cookie_names(content)
    found = tuple(sorted({m for n in names if (m := _matches_required(n))}))

    if not found:
        return CookieValidation(
            ok=False,
            found=(),
            missing=REQUIRED_COOKIES,
            error=(
                "Tidak ada cookie autentikasi YouTube di berkas ini. Pastikan Anda "
                "mengekspornya SAAT sudah login ke YouTube. Minimal salah satu dari: "
                + ", ".join(REQUIRED_COOKIES)
            ),
        )

    missing = tuple(name for name in REQUIRED_COOKIES if name not in found)
    return CookieValidation(ok=True, found=found, missing=missing)


def validate_cookie_file(path: str | Path) -> CookieValidation:
    """Validasi berkas cookies.txt di disk."""
    file_path = Path(path)
    if not file_path.exists():
        return CookieValidation(ok=False, error="Berkas cookies tidak ditemukan.")
    if not file_path.is_file():
        return CookieValidation(ok=False, error="Jalur cookies bukan sebuah berkas.")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CookieValidation(ok=False, error=f"Gagal membaca berkas cookies: {exc}")

    return validate_cookie_content(content)


def cookie_warning(validation: CookieValidation) -> str:
    """Pesan peringatan untuk ditampilkan ke pengguna, kosong bila tidak ada.

    Cookies yang berfungsi tetapi tidak lengkap tetap dicatat: bila unduhan
    gagal di kemudian hari, pesan ini menjelaskan kenapa.
    """
    if not validation.ok:
        return validation.error
    if validation.missing:
        return (
            "Cookies diterima, tetapi sebagian cookie umum tidak ada ("
            + ", ".join(validation.missing)
            + "). Unduhan mungkin tetap gagal pada video terbatas."
        )
    return ""
