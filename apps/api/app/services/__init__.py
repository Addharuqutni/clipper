"""Lapisan service API: logika non-HTTP (query basis data, aturan bisnis, validasi).

Route di :mod:`app.api.v1` hanya mem-parse request, memanggil service, dan
memetakan kesalahan domain ke respons HTTP (lihat :mod:`app.services.errors`).
Pemisahan ini membuat aturan bisnis dapat diuji tanpa klien HTTP dan menjaga
route tetap tipis.
"""
