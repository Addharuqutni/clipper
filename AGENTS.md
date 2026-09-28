# AGENTS.md

## Bahasa

Semua kesimpulan, ringkasan, laporan akhir, dan pertanyaan kepada pengguna MUST ditulis dalam Bahasa Indonesia. Kode, nama simbol, pesan commit, dan isi file teknis mengikuti konvensi yang sudah ada di repo.

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/` (no git remote). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), recorded as a `Status:` line. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
