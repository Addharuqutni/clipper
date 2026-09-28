// ClipperAI — satu perintah untuk development di Windows: `npm run dev`.
//
// 1. scripts\setup.cmd (idempoten: venv, pip, FFmpeg, model, npm, .env).
// 2. API + WEB di SATU terminal, log diberi awalan [api]/[web]. Pipeline
//    (unduh, transkripsi, render) berjalan di dalam proses API; data di SQLite.
// 3. Ctrl+C (atau menutup terminal) menghentikan semuanya.
//
// Proses anak memakai launcher run-*.cmd, jadi env, PYTHONPATH, dan perintahnya
// hanya ditulis di satu tempat. Log anak dialirkan lewat pipe ke jendela ini.
//
// Tiap anak mendapat KONSOL TERSEMBUNYI sendiri (`windowsHide` tanpa stdio
// warisan = CREATE_NO_WINDOW): Ctrl+C di jendela ini tidak sampai ke anak, jadi
// penghentian selalu lewat taskkill di stopAll() — seluruh pohon proses.
//
// Jangan pakai `detached`: itu DETACHED_PROCESS (tanpa konsol sama sekali), dan
// cmd.exe lalu menggantung pada perintah ber-pipe (`findstr | findstr` di _env.cmd).
import { spawn, spawnSync } from "node:child_process";
import net from "node:net";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

if (process.platform !== "win32") {
  console.error("scripts/dev.mjs khusus Windows.");
  process.exit(1);
}

const scripts = path.dirname(fileURLToPath(import.meta.url));
const cmd = (line, options = {}) =>
  spawnSync("cmd.exe", ["/d", "/s", "/c", `"${line}"`], {
    windowsVerbatimArguments: true,
    ...options,
  });

// Dari start.cmd (`--open`) jendela milik proses ini sendiri: tahan saat error
// supaya pesannya terbaca sebelum jendela tertutup.
const launchedByStartCmd = process.argv.includes("--open");
/** Keluar; saat error dari start.cmd, tunggu Enter dulu. Promise-nya tidak pernah selesai. */
function exit(code) {
  if (code === 0 || !launchedByStartCmd) process.exit(code);
  console.log("\n  [X] Berhenti dengan error. Tekan Enter untuk menutup jendela.");
  process.stdin.resume();
  process.stdin.once("data", () => process.exit(code));
  return new Promise(() => {});
}

const setup = cmd(`"${path.join(scripts, "setup.cmd")}"`, { stdio: "inherit" });
if (setup.status !== 0) await exit(setup.status ?? 1);

const services = [
  ["api", "36", "run-api.cmd"],
  ["web", "35", "run-web.cmd"],
];

console.log("\n  Frontend : http://localhost:3000");
console.log("  API      : http://localhost:8000/docs");
console.log("  Ctrl+C (atau tutup jendela ini) untuk menghentikan semuanya.\n");

// `--open` (dipakai start.cmd): buka browser begitu frontend menerima koneksi.
if (process.argv.includes("--open")) {
  const tryOpen = () => {
    const socket = net.connect(3000, "localhost");
    socket.once("connect", () => {
      socket.destroy();
      spawn("cmd.exe", ["/d", "/c", "start", "", "http://localhost:3000"], { stdio: "ignore" });
    });
    socket.once("error", () => setTimeout(tryOpen, 1000));
  };
  tryOpen();
}

const children = [];
let stopping = false;

function stopAll(code) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (child.exitCode === null) {
      spawnSync("taskkill", ["/T", "/F", "/PID", String(child.pid)], { stdio: "ignore" });
    }
  }
  exit(code);
}

/** Kode keluar Windows untuk proses yang dihentikan Ctrl+C (0xC000013A). */
const STATUS_CONTROL_C_EXIT = 0xc000013a;

for (const [name, color, launcher] of services) {
  const [file, ...args] = launcher.split(" ");
  const child = spawn(
    "cmd.exe",
    ["/d", "/s", "/c", `""${path.join(scripts, file)}" ${args.join(" ")}"`],
    { windowsVerbatimArguments: true, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  children.push(child);
  const prefix = `\x1b[${color}m[${name.padEnd(6)}]\x1b[0m `;
  for (const stream of [child.stdout, child.stderr]) {
    readline.createInterface({ input: stream }).on("line", (line) => {
      // Gema Ctrl+C dari cmd.exe anak ("^C", "Terminate batch job (Y/N)?") bukan log layanan.
      if (!stopping && !/^(\^C|Terminate batch job)/.test(line.trim())) console.log(prefix + line);
    });
  }
  child.on("exit", (code) => {
    if (stopping) return;
    // Ctrl+C sampai ke anak (satu konsol) dan bisa diproses sebelum SIGINT di sini:
    // itu penghentian normal, bukan crash.
    if (code === STATUS_CONTROL_C_EXIT) return stopAll(0);
    console.log(
      `${prefix}berhenti (kode ${code}). Menghentikan layanan lain... ` +
        "Bila port 8000/3000 masih terpakai, jalankan stop.cmd.",
    );
    stopAll(code || 1);
  });
}

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"]) {
  process.on(signal, () => stopAll(0));
}
