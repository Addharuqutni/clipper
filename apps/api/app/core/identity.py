"""Identitas pengguna lokal — satu-satunya pengguna di instalasi ini.

**Konteks.** Aplikasi ini hanya berjalan di satu mesin, dipakai satu orang.
Autentikasi dihapus sepenuhnya (lihat ``app/api/v1/auth.py``), jadi tidak ada
lagi login, JWT, atau pemisahan data antar-pengguna.

**Mengapa tetap ada baris di tabel ``users``.** Bukan sisa warisan: tujuh tabel
mempunyai foreign key ke ``users.id`` dengan ``ON DELETE CASCADE``:

    jobs, overlay_assets, ai_provider_settings, font_assets,
    subtitle_presets, social_accounts, scheduled_posts

Tanpa baris ini, ``POST /jobs`` gagal dengan ``ForeignKeyViolationError``.
Baris dibuat otomatis lewat :func:`ensure_local_user`, sehingga basis data boleh
dikosongkan kapan saja tanpa merusak aplikasi.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

#: UUID tetap, sengaja bukan acak: job yang dibuat sebelum dan sesudah
#: pengosongan basis data harus tetap dianggap milik pengguna yang sama, dan
#: baris ``users`` hanya boleh dibuat sekali.
LOCAL_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
#: Bentuk email harus lolos validasi ``EmailStr`` pydantic, yang menolak
#: domain reserved: ``local@localhost``, ``local@clipper.local``, dan
#: ``local@clipper.invalid`` SEMUANYA ditolak dengan pesan "special-use or
#: reserved name" — dan muncul sebagai 500 dari dalam UserResponse, bukan
#: pesan yang bisa ditindaklanjuti. Penolakannya terjadi di level validator,
#: bukan sekadar format, sehingga tidak bisa diakali dengan memilih TLD lain
#: yang juga reserved. Domain normal dipakai agar validasi lolos.
LOCAL_USER_EMAIL = "local@clipper.ai"


async def ensure_local_user(session: AsyncSession) -> None:
    """Buat baris ``users`` untuk pengguna lokal bila belum ada.

    Idempoten: mengecek keberadaan pengguna dan menambahkannya bila belum ada.
    Kompatibel 100% dengan PostgreSQL dan SQLite. Pemanggil bertanggung jawab
    melakukan ``commit``.

    Kolom ``hashed_password`` diisi penanda yang tidak dapat dicocokkan —
    akun ini tidak punya kata sandi dan tidak ada jalur login untuk memakainya,
    tetapi kolomnya ``NOT NULL`` di skema.
    """
    from app.models.user import User

    user = await session.get(User, LOCAL_USER_ID)
    if user is None:
        session.add(
            User(
                id=LOCAL_USER_ID,
                email=LOCAL_USER_EMAIL,
                # Penanda akun tanpa kata sandi, bukan kredensial.
                hashed_password="!local-account-no-password!",  # noqa: S106
                plan="local",
            )
        )
