// Validasi URL YouTube murni (tanpa React). TECH_SPEC Sprint 1 butir 13, PRD FR-1.1.
// Menerima watch?v=, shorts/ (sama dengan server), dan youtu.be/.
export function isValidYoutubeUrl(raw: string): boolean {
  const s = raw.trim();
  if (!s) return false;
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  const host = u.hostname.toLowerCase().replace(/^www\./, "");
  // Bentuk 1: youtube.com/watch?v=<11 char>
  if (host === "youtube.com" || host === "m.youtube.com") {
    if (u.pathname.startsWith("/shorts/")) return /^[\w-]{11}$/.test(u.pathname.split("/")[2] ?? "");
    if (u.pathname !== "/watch") return false;
    const v = u.searchParams.get("v");
    return v !== null && /^[\w-]{11}$/.test(v);
  }
  // Bentuk 2: youtu.be/<11 char>
  if (host === "youtu.be") {
    const id = u.pathname.replace(/^\/+/, "").split("/")[0] ?? "";
    return /^[\w-]{11}$/.test(id);
  }
  return false;
}

// Ambil ID video bila valid, null bila tidak.
export function extractYoutubeId(raw: string): string | null {
  if (!isValidYoutubeUrl(raw)) return null;
  const u = new URL(raw.trim());
  const host = u.hostname.toLowerCase().replace(/^www\./, "");
  if (host === "youtu.be") return u.pathname.replace(/^\/+/, "").split("/")[0];
  if (u.pathname.startsWith("/shorts/")) return u.pathname.split("/")[2];
  return u.searchParams.get("v");
}
