"""Guard lokasi `TokenCipher`: HARUS dapat diimpor worker tanpa `apps/api`.

Latar belakang — bug yang test ini cegah agar tidak kembali.

``TokenCipher`` dulu tinggal di ``apps/api/app/core/security.py``, sementara
worker-light mendekripsikan kunci API penyedia (dan cookies YouTube) dengan:

    from app.core.security import EncryptedBlob, TokenCipher

Impor itu gagal setiap kali ``apps/api`` tidak ada di ``PYTHONPATH``. Kegagalan
tertangkap ``except Exception`` yang lebar di ``_decrypt_provider_key``, yang
hanya menulis peringatan lalu mengembalikan ``""``. Akibatnya:

* kunci API yang benar-benar tersimpan di database menjadi kosong;
* skoring mati dengan pesan **"Penyedia Google Gemini memerlukan API key"** —
  menyesatkan, karena kuncinya ada;
* waktu dihabiskan untuk memeriksa database, setelan UI, dan koneksi penyedia,
  padahal akarnya satu impor yang gagal.

Karena itu modul dipindah ke ``clipper_shared.security``: satu-satunya paket
yang di-distribusikan ke SEMUA container. Test di bawah mengunci sifat itu.

Yang diuji BUKAN perilaku kripto (itu tugas ``apps/api/tests/test_security_aesgcm.py``),
melainkan **di mana modul itu boleh tinggal dan bagaimana ia diimpor**.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

# Root repo = dua level di atas packages/shared/tests/.
REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_SRC = REPO_ROOT / "packages" / "shared" / "src"
WORKER_LIGHT = REPO_ROOT / "apps" / "worker-light"
WORKER_RENDER = REPO_ROOT / "apps" / "worker-render"


class TestImportableFromWorkerPath:
    """`clipper_shared.security` harus terimpor hanya dengan jalur worker."""

    def test_impor_dari_jalur_worker_tanpa_apps_api(self) -> None:
        """Syarat utama: TANPA ``apps/api`` di ``sys.path``, impor tetap berhasil.

        Inilah kondisi nyata worker. Sebelum perbaikan, subprocess ini gagal
        dengan ``ModuleNotFoundError: No module named 'app'``.
        """
        code = (
            "from clipper_shared.security import EncryptedBlob, TokenCipher;"
            "print('ok')"
        )
        env_path = f"{WORKER_LIGHT}{__import__('os').pathsep}{SHARED_SRC}"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            env={"PYTHONPATH": env_path, "PATH": __import__("os").environ.get("PATH", "")},
        )
        assert result.returncode == 0, (
            "clipper_shared.security tidak dapat diimpor dengan jalur worker.\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

    def test_dekripsi_kunci_penyedia_tanpa_apps_api(self) -> None:
        """Round-trip enkripsi/dekripsi harus jalan di lingkungan worker.

        Meniru ``_decrypt_provider_key``: baca kunci dari env, dekripsi dengan
        AAD yang mengikat (user, "ai_provider", "api_key_encrypted"). Kalau
        modul ini tidak dapat diimpor, test gagal di sini — bukan jauh di hilir
        dengan pesan yang menyesatkan.
        """
        from clipper_shared.security import EncryptedBlob, TokenCipher, generate_key_b64

        key_b64 = generate_key_b64()
        cipher = TokenCipher.from_b64_key(key_b64)
        aad = TokenCipher.aad_for("user-1", "ai_provider", "api_key_encrypted")
        blob = cipher.encrypt("sk-rahasia-123", aad=aad)

        assert cipher.decrypt(EncryptedBlob.from_b64(blob.to_b64()), aad=aad) == "sk-rahasia-123"


class TestNoCrossAppImports:
    """Kode worker TIDAK boleh mengimpor paket milik API (``app.*``).

    Impor lintas-aplikasi seperti itu rapuh: ia hanya "kebetulan" bekerja ketika
    direktori ``apps/api`` ada di ``sys.path``.
    """

    @pytest.mark.parametrize(
        "app_dir", [WORKER_LIGHT, WORKER_RENDER], ids=["worker-light", "worker-render"]
    )
    def test_tidak_ada_impor_app_dot(self, app_dir: Path) -> None:
        """Telusuri AST agar impor di dalam fungsi juga ikut terperiksa.

        Pencarian teks biasa akan melewatkan impor bersarang (dan menandai
        komentar sebagai positif palsu) — karena itu dipakai ``ast``.
        """
        pelanggaran: list[str] = []

        for path in sorted(app_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "app" or alias.name.startswith("app."):
                            pelanggaran.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    # level > 0 berarti impor relatif (aman, tetap di paketnya).
                    if node.level == 0 and (module == "app" or module.startswith("app.")):
                        pelanggaran.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> from {module} import ...")

        assert not pelanggaran, (
            "Kode worker mengimpor paket milik API (`app.*`). Pindahkan kode yang "
            "dibutuhkan ke `clipper_shared` — di situlah kode bersama harus tinggal.\n"
            + "\n".join(f"  - {p}" for p in pelanggaran)
        )
