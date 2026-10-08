"""Prompt scoring LLM yang berversi (PRD FR-2.2).

Prompt sengaja dipisah dari :mod:`worker_light.scoring_client` karena dua alasan:

1. **Rubrik adalah aset yang di-tuning, bukan logika.** Ia akan diubah
   berkali-kali saat dievaluasi, sedangkan kode pemanggilnya hampir tidak
   pernah berubah. Menyatukan keduanya membuat tiap eksperimen rubrik
   tercampur dengan diff logika.
2. **Versi harus bisa direkam.** Tanpa konstanta versi, tidak ada cara
   membandingkan dua hasil scoring yang dihasilkan prompt berbeda — dan itu
   membuat semua klaim "kualitas meningkat" tidak dapat dibuktikan.

Definisi tiga dimensi di bawah diambil **persis** dari PRD FR-2.2, bukan
dikarang: rubrik yang tidak berdasar spesifikasi hanya mengganti tebakan
model dengan tebakan kita.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AGENT_PROMPT_VERSION",
    "LLM_MOOD_FALLBACK",
    "build_mood_prompt",
    "MAX_LABEL_HINT_CHARS",
    "SCORING_PROMPT_VERSION",
    "build_critic_prompt",
    "build_judge_prompt",
    "build_scoring_prompt",
    "build_scout_prompt",
    "build_system_prompt",
]

#: Versi rubrik. Naikkan bila definisi di bawah berubah, supaya hasil scoring
#: lama dan baru tidak tertukar saat dievaluasi.
#:
#: v1 — definisi awal dari PRD FR-2.2 saja.
#: v2 — ditambah hasil distilasi 9 klip viral: hook berupa pertanyaan yang
#:      menantang, struktur setup-gesekan-payoff, monolog pembuka dihindari,
#:      dan akhir klip boleh menggantung maupun tuntas.
SCORING_PROMPT_VERSION = "v2"

#: Versi prompt agent pemilihan momen (3 pass). Terpisah dari
#: :data:`SCORING_PROMPT_VERSION` karena ia versi **strategi**, bukan versi
#: rubrik penilaian — dua hal yang berubah untuk alasan berbeda.
AGENT_PROMPT_VERSION = "v1"

#: Batas panjang label yang diminta ke model. Lebih pendek dari
#: ``MAX_LABEL_CHARS`` (64) agar hasil pemotongan di kode tidak memotong
#: kata di tengah.
MAX_LABEL_HINT_CHARS = 48

#: Definisi operasional tiga dimensi penilaian (PRD FR-2.2).
#:
#: Sengaja ditulis sebagai pasangan "tinggi / rendah". Model memberi skor yang
#: jauh lebih konsisten bila diberi contoh konkret daripada bila hanya diberi
#: nama metriknya.
_RUBRIC = """Kriteria penilaian — nilai tiap kandidat pada tiga dimensi (0.0-1.0):

HOOK — apakah 3 detik pertama memberi alasan untuk tetap menonton?
  Tinggi: diawali pertanyaan yang MENANTANG atau menguji (tes, tebak-tebakan,
          perintah "sebutin..."), klaim mengejutkan, angka/fakta spesifik,
          atau langsung di tengah aksi yang sudah berjalan.
          Memulai klip DI TENGAH kalimat atau di tengah aksi justru baik —
          itu tanda percakapan sudah berjalan, bukan kesalahan potongan.
  Rendah: diawali salam, "jadi gini ya", "ehm", keraguan, penyebutan episode
          sebelumnya, atau MONOLOG NARASI PANJANG yang menjelaskan latar
          ("pada saat itu saya sedang...") sebelum masuk ke intinya.

COMPLETENESS — bisakah klip ini berdiri sendiri?
  Tinggi: memuat rangkaian utuh SETUP -> GESEKAN -> PAYOFF. Setup berupa
          pemicu (pertanyaan/tes/tantangan); gesekan berupa penolakan,
          ragu, atau debat singkat antar pembicara; payoff berupa jawaban
          rahasia, punchline, keputusan, atau hadiah.
  Rendah: berhenti sebelum payoff keluar, menyebut "seperti yang tadi saya
          bilang", atau memerlukan bagian lain video agar bermakna.

EMOTIONAL ARC — adakah perubahan intensitas dari awal ke akhir?
  Tinggi: tenang lalu memanas, pertanyaan lalu jawaban, masalah lalu
          solusi, tegang lalu lega, atau reaksi spontan yang jelas
          (tawa, kaget, kepasrahan).
  Rendah: datar dari awal ke akhir, murni paparan fakta, atau daftar
          yang tidak berujung pada apa pun.

TENTANG AKHIR KLIP — dua-duanya sah, pilih yang paling kuat:
  * menggantung: berhenti tepat setelah reaksi spontan atau saat narasumber
    membuka topik baru, sebelum gagasan kedua selesai;
  * tuntas: ditutup 1-2 detik setelah punchline atau jawaban keluar, saat
    tawa atau kepasrahan terdengar.
  Jangan memotong sebelum payoff, dan jangan menyisakan bagian yang hanya
  berisi ucapan terima kasih atau perpisahan.

Aturan penolakan — jangan ajukan kandidat yang:
  * merupakan intro/outro, ajakan like/subscribe, atau sponsor read;
  * membahas urusan teknis (mikrofon, koneksi, jeda, kesalahan edit);
  * berisi jeda panjang atau bagian yang tidak ada percakapannya;
  * hanya basa-basi tanpa gagasan yang selesai.

Batas waktu mengikuti jeda wajar percakapan. Namun bila pilihannya antara
memulai sedikit di tengah kalimat atau memulai dengan monolog penjelasan,
PILIH yang di tengah kalimat."""

#: Aturan bentuk keluaran. Dipisah dari rubrik karena ini kontrak mesin,
#: bukan panduan penilaian — model perlu membedakan keduanya.
#:
#: Kurung kurawal contoh JSON digandakan (``{{``) karena teks ini diproses
#: :meth:`str.format`; tanpa itu ``{"segments": ...}`` dibaca sebagai
#: placeholder dan memunculkan ``KeyError``.
_OUTPUT_CONTRACT = """Balas HANYA dengan JSON valid. Tanpa penjelasan tambahan, tanpa pagar markdown.

Format keluaran:
{{"segments": [{{"start_s": <detik>, "end_s": <detik>, "score": <0-100>, "label": "<maks {label_chars} karakter>", "hook_score": <0-1>, "completeness": <0-1>, "emotional_arc": <0-1>, "reason": "<satu kalimat>"}}]}}

Aturan bentuk:
* tiap segmen berdurasi {min_s:.0f}-{max_s:.0f} detik;
* tidak saling tumpang tindih;
* urut menaik berdasarkan start_s;
* "score" adalah 0-100, sedangkan hook_score/completeness/emotional_arc
  adalah 0.0-1.0 — jangan tertukar;
* lebih baik mengembalikan sedikit segmen yang benar-benar kuat daripada
  memaksakan jumlah dengan kandidat yang lemah."""


def build_system_prompt(
    *,
    version: str = SCORING_PROMPT_VERSION,
    min_s: float,
    max_s: float,
    label_chars: int = MAX_LABEL_HINT_CHARS,
) -> str:
    """Susun pesan sistem: rubrik penilaian + kontrak keluaran.

    Args:
        version: versi rubrik; hanya memengaruhi jejak, bukan isi.
        min_s: durasi segmen minimum yang diminta ke model.
        max_s: durasi segmen maksimum yang diminta ke model.
        label_chars: batas panjang label yang diminta.

    Returns:
        Teks prompt sistem.

    Raises:
        ValueError: bila ``version`` tidak dikenal.
    """
    if version != SCORING_PROMPT_VERSION:
        raise ValueError(f"Versi rubrik tidak dikenal: {version!r}")

    contract = _OUTPUT_CONTRACT.format(
        min_s=min_s, max_s=max_s, label_chars=label_chars
    )
    return f"{_RUBRIC}\n\n{contract}"


def build_scoring_prompt(
    *,
    transcript: str,
    target_count: int,
    min_s: float,
    max_s: float,
    user_direction: str = "",
    output_language: str = "Bahasa Indonesia",
    version: str = SCORING_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    """Susun pesan untuk model, dengan rubrik penilaian eksplisit.

    Susunannya penting: **transkrip lebih dulu, arahan pengguna terakhir**.
    Transkrip podcast 60 menit dapat melewati 100 ribu karakter, dan model
    memberi bobot lebih besar pada bagian akhir — arahan yang diletakkan
    sebelum transkrip akan diabaikan.

    Args:
        transcript: transkrip bertimestamp hasil ``_render_transcript``.
        target_count: jumlah segmen yang diminta (pemanggil sudah menambah
            cadangan di luar fungsi ini).
        min_s: durasi segmen minimum.
        max_s: durasi segmen maksimum.
        user_direction: arahan bebas pengguna, sudah dibersihkan.
        output_language: bahasa untuk label dan alasan.
        version: versi rubrik.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_system_prompt(
                version=version, min_s=min_s, max_s=max_s
            ),
        },
        {
            "role": "user",
            "content": (
                f"Temukan sekitar {target_count} segmen terbaik dari transkrip berikut.\n"
                f"Tulis label dan alasan dalam {output_language}.\n\n"
                f"--- TRANSKRIP ---\n{transcript}"
            ),
        },
    ]

    if user_direction:
        messages.append(
            {
                "role": "user",
                "content": (
                    "--- ARAHAN PENGGUNA ---\n"
                    f"{user_direction}\n"
                    "--- AKHIR ARAHAN ---\n"
                    "Ikuti arahan di atas saat memilih segmen."
                ),
            }
        )

    return messages


# --------------------------------------------------------------------------
# Prompt agent pemilihan momen (3 pass)
#
# Dipisah dari prompt satu-pass di atas karena strateginya beda: di sini model
# dipanggil tiga kali dengan tugas yang berbeda-beda. Kontraknya:
#
#   SCOUT  — cari kandidat dari jendela kecil. Recall tinggi, murah.
#   JUDGE  — nilai satu kandidat dengan teks utuhnya. Presisi tinggi.
#   CRITIC — buang kandidat yang tumpang tindih topiknya.
#
# Mengapa dipecah: pada satu pass, model harus memilih dari transkrip 60 menit
# yang sudah diringkas per 12 detik. Ia memilih dari **ringkasan momen**, bukan
# dari momen itu sendiri. Di pass JUDGE ia membaca teks utuh kandidat, dan input
# per panggilan jauh lebih kecil — jadi meski jumlah panggilan bertambah, tiap
# panggilan lebih murah dan penilaiannya lebih tajam.
# --------------------------------------------------------------------------

#: Kontrak keluaran SCOUT. Angka durasi tidak diminta presisi di sini: tugas
#: pass ini hanya menandai **di mana** momennya, bukan menentukan batas akhirnya.
_SCOUT_CONTRACT = """Balas HANYA dengan JSON valid, tanpa penjelasan dan tanpa pagar markdown.

{{"candidates": [{{"start_s": <detik>, "end_s": <detik>, "label": "<maks {label_chars} karakter>"}}]}}

Aturan:
* hanya tandai bagian yang benar-benar punya gagasan yang selesai;
* batas awal/akhir boleh kasar, yang penting momennya tercakup;
* urut menaik berdasarkan start_s;
* lebih baik sedikit kandidat yang kuat daripada banyak yang lemah;
* kosongkan "candidates" bila tidak ada yang layak di bagian ini."""

#: Kontrak keluaran JUDGE. Di pass ini model diberi teks utuh satu kandidat,
#: jadi ia bisa menilai kalimat demi kalimat, bukan dari ringkasan.
_JUDGE_CONTRACT = """Balas HANYA dengan JSON valid, tanpa penjelasan dan tanpa pagar markdown.

{{"start_s": <detik awal yang disarankan>, "end_s": <detik akhir yang disarankan>, "score": <0-100>, "label": "<maks {label_chars} karakter>", "hook_score": <0-1>, "completeness": <0-1>, "emotional_arc": <0-1>, "reason": "<satu kalimat>"}}

Aturan:
* "score" 0-100, sedangkan hook_score/completeness/emotional_arc 0.0-1.0;
* Anda BOLEH menggeser batas awal/akhir bila itu membuat klip berdiri sendiri,
  tetapi jangan melebihi {min_s:.0f}-{max_s:.0f} detik;
* batas harus jatuh pada jeda wajar percakapan, bukan di tengah kalimat;
* skor di bawah {reject_below} berarti "jangan dipakai" — jangan dipaksakan tinggi."""

#: Kontrak keluaran CRITIC. Tugasnya mengurangi, bukan menambah.
_CRITIC_CONTRACT = """Balas HANYA dengan JSON valid, tanpa penjelasan dan tanpa pagar markdown.

{{"drop": [<indeks kandidat yang dibuang, mulai 0>]}}

Aturan:
* buang kandidat yang topiknya tumpang tindih dengan kandidat lain — pertahankan yang terkuat;
* buang kandidat yang tidak berdiri sendiri tanpa konteks luar;
* buang kandidat yang merupakan intro/outro/ajakan subscribe/sponsor;
* kosongkan "drop" bila semuanya layak dipertahankan;
* jangan buang hanya karena ada terlalu banyak kandidat."""


def build_scout_prompt(
    *,
    transcript: str,
    max_candidates: int,
    output_language: str = "Bahasa Indonesia",
    label_chars: int = MAX_LABEL_HINT_CHARS,
) -> list[dict[str, Any]]:
    """Susun prompt pass SCOUT: tandai momen layak dalam satu jendela.

    Args:
        transcript: Transkrip satu jendela, sudah bertimestamp.
        max_candidates: Jumlah maksimum kandidat yang diminta.
        output_language: Bahasa untuk label.
        label_chars: Batas panjang label.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    return [
        {
            "role": "system",
            "content": (
                "Tugas Anda: menandai momen dalam potongan transkrip yang layak "
                "dijadikan klip pendek vertikal. Anda hanya menandai lokasinya; "
                "penilaian dilakukan di tahap lain.\n\n"
                + _RUBRIC
                + "\n\n"
                + _SCOUT_CONTRACT.format(label_chars=label_chars)
            ),
        },
        {
            "role": "user",
            "content": (
                f"Tandai paling banyak {max_candidates} momen dari potongan transkrip ini.\n"
                f"Tulis label dalam {output_language}.\n\n"
                f"--- TRANSKRIP ---\n{transcript}"
            ),
        },
    ]


def build_judge_prompt(
    *,
    candidate_transcript: str,
    start_s: float,
    end_s: float,
    min_s: float,
    max_s: float,
    reject_below: int,
    output_language: str = "Bahasa Indonesia",
    label_chars: int = MAX_LABEL_HINT_CHARS,
) -> list[dict[str, Any]]:
    """Susun prompt pass JUDGE: nilai satu kandidat dari teks utuhnya.

    Args:
        candidate_transcript: Teks kandidat, bertimestamp, tidak diringkas.
        start_s: Awal kandidat hasil SCOUT.
        end_s: Akhir kandidat hasil SCOUT.
        min_s: Durasi minimum yang diizinkan.
        max_s: Durasi maksimum yang diizinkan.
        reject_below: Skor di bawah ini berarti kandidat tidak layak.
        output_language: Bahasa untuk label dan alasan.
        label_chars: Batas panjang label.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    return [
        {
            "role": "system",
            "content": (
                "Tugas Anda: menilai SATU kandidat klip berdasarkan teks utuhnya, "
                "lalu menyarankan batas akhir yang membuat klip berdiri sendiri.\n\n"
                + _RUBRIC
                + "\n\n"
                + _JUDGE_CONTRACT.format(
                    label_chars=label_chars, min_s=min_s, max_s=max_s, reject_below=reject_below
                )
            ),
        },
        {
            "role": "user",
            "content": (
                f"Nilai kandidat ini (ditandai sekitar {start_s:.1f}-{end_s:.1f} detik).\n"
                f"Tulis label dan alasan dalam {output_language}.\n\n"
                f"--- TEKS KANDIDAT ---\n{candidate_transcript}"
            ),
        },
    ]


def build_critic_prompt(
    *,
    candidates_text: str,
    keep_count: int,
    output_language: str = "Bahasa Indonesia",
) -> list[dict[str, Any]]:
    """Susun prompt pass CRITIC: buang kandidat yang lemah atau redundan.

    Args:
        candidates_text: Ringkasan kandidat, satu baris per kandidat, sudah
            bernomor indeks.
        keep_count: Jumlah kandidat yang diharapkan tersisa.
        output_language: Bahasa untuk instruksi tambahan.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    return [
        {
            "role": "system",
            "content": (
                "Tugas Anda: menyisir daftar kandidat klip dan membuang yang tidak "
                "layak dipertahankan. Anda mengurangi, bukan menambah.\n\n"
                + _CRITIC_CONTRACT
            ),
        },
        {
            "role": "user",
            "content": (
                f"Berikut {keep_count} kandidat terbaik yang tersisa. Mana yang harus dibuang?\n"
                f"Tulis ringkasan dalam {output_language} bila perlu.\n\n"
                f"--- KANDIDAT ---\n{candidates_text}"
            ),
        },
    ]


#: Kategori suasana untuk klasifikasi berbasis LLM (opsi B).
#:
#: Sengaja tidak memakai kategori ``sedih``: dari transkrip teks saja, sedih
#: hampir tidak bisa dibedakan dari serius. Keduanya butuh nada bicara atau
#: ekspresi wajah — dan keduanya tidak tersedia di sini.
LLM_MOOD_CATEGORIES: tuple[str, ...] = (
    "komedi",
    "emosional",
    "menegangkan",
    "informatif",
    "hangat",
    "netral",
)

#: Nilai jatuh-tempo bila LLM gagal atau membalas di luar kategori.
LLM_MOOD_FALLBACK = "netral"

#: Kontrak keluaran klasifikasi suasana. Satu kata saja — model sering
#: menambahkan penjelasan yang merusak parsing.
_MOOD_CONTRACT = """Balas HANYA dengan satu kata: kategori suasana dari transkrip ini.

Pilih tepat SATU dari: {categories}

Arti kategori:
* komedi — humor, lelucon, tawa, olok-olok;
* emosional — cerita personal yang menyentuh, pengakuan, kekecewaan;
* menegangkan — konflik, ketegangan, ketidakpastian, kejutan;
* informatif — penjelasan, fakta, instruksi, berita;
* hangat — kebersamaan, persahabatan, kemesraan, nostalgia;
* netral — tidak ada suasana yang menonjol.

Aturan:
* tentukan dari ISI BICARA, bukan dari teks semata — "aku kehilangan ayahku"
  adalah emosional walaupun tidak ada kata sedih;
* bila ragu antara dua kategori, pilih yang lebih menonjol di AKHIR segmen,
  karena itulah yang dirasakan penonton saat klip berakhir;
* jangan menulis penjelasan, tanda baca, atau huruf lain."""


def build_mood_prompt(
    *,
    transcript: str,
    categories: tuple[str, ...] = LLM_MOOD_CATEGORIES,
) -> list[dict[str, Any]]:
    """Susun prompt klasifikasi suasana berbasis LLM.

    Dipakai untuk kategori yang tidak punya penanda reaksi di transkrip
    (``emosional``, ``menegangkan``, ``informatif``, ``hangat``). Kategori
    ``komedi``/``musik``/``reaksi`` sebaiknya tetap memakai
    :mod:`clipper_shared.mood` yang deterministik.

    Args:
        transcript: Teks transkrip segmen.
        categories: Kategori yang ditawarkan; bawaan
            :data:`LLM_MOOD_CATEGORIES`.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    return [
        {
            "role": "system",
            "content": _MOOD_CONTRACT.format(categories=", ".join(categories)),
        },
        {"role": "user", "content": f"--- TRANSKRIP ---\n{transcript}"},
    ]
