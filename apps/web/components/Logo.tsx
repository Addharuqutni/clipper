// Mark ClipperAI — simbol "Cut Frame": bingkai 9:16 yang sudut kanan-atasnya
// dipotong 45 derajat. Digambar sebagai path di dalam komponen (bukan <img>)
// supaya bisa ikut warna teks lewat `currentColor` dan tidak butuh berkas
// tambahan. Geometri identik dengan master di `app/icon.svg`.
//
// Dua gambar: `full` (dengan jendela) untuk ukuran besar, `small` (tanpa
// jendela) untuk 16–24 px, karena jendela 30 unit mengempis jadi bercak.
const PATHS = {
  full: "M69 24H154L186 56V232H69Z M99 54H142L156 68V202H99Z",
  small: "M69 24H154L186 56V232H69Z",
} as const;

export function Logo({
  size = 26,
  detail = "full",
  title = "ClipperAI",
}: {
  size?: number;
  detail?: keyof typeof PATHS;
  title?: string;
}) {
  return (
    <svg
      role="img"
      aria-label={title}
      width={size}
      height={(size * 208) / 117}
      viewBox="69 24 117 208"
      fill="currentColor"
      fillRule="evenodd"
    >
      <path d={PATHS[detail]} />
    </svg>
  );
}
