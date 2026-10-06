"""Isolasi test API dari data pengembangan.

``settings`` membaca ``.env`` root repo, jadi tanpa ini test yang membuat job
(mis. ``test_dispatch``) menulis ke basis data pengembang sungguhan — 31 job uji
``youtu.be/dQw4w9WgXcQ`` pernah menumpuk di dashboard. Env proses menang atas
``.env``, dan harus diset sebelum ``app`` diimpor karena ``settings`` dibaca
sekali saat impor.

``testserver`` (host bawaan TestClient) diizinkan lewat ``ALLOWED_HOSTS``;
di aplikasi sungguhan hanya localhost yang diterima.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_tmp = tempfile.mkdtemp(prefix="clipper-test-")
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

os.environ.update(
    {
        "DATABASE_URL": "",
        "LOCAL_STORAGE_DIR": _tmp,
        "WORKER_WORKSPACE_DIR": f"{_tmp}/work",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver",
        # Kunci uji tetap (32 byte nol); bukan rahasia.
        "TOKEN_ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
)
