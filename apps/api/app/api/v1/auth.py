"""Dependency identitas dan kepemilikan untuk pemakaian lokal (satu pengguna, tanpa login).

Baris ``users`` tetap ada karena tabel lain (``jobs``, ``overlay_assets``,
``ai_provider_settings``, ...) mempunyai foreign key ke ``users.id``. Baris itu
dibuat otomatis saat startup dan saat pertama kali dibutuhkan.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import LOCAL_USER_ID, ensure_local_user
from app.db.session import get_db
from app.models.job import Job
from app.models.user import User

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(db: DbSession) -> User:
    """Kembalikan pengguna lokal, membuat barisnya bila belum ada. Tidak pernah 401."""
    await ensure_local_user(db)
    await db.commit()
    user = (await db.execute(select(User).where(User.id == LOCAL_USER_ID))).scalar_one_or_none()
    if user is None:  # pragma: no cover - hanya bila baris dihapus di tengah request
        raise RuntimeError("Baris pengguna lokal hilang tepat setelah dibuat.")
    return user


#: Dependency pengguna yang dipakai seluruh router.
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserOrDev = CurrentUser


async def get_owned_job(db: AsyncSession, user_id: Any, job_id: UUID) -> Job:
    """Ambil job milik pengguna, atau 404."""
    job = (await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan")
    return job
