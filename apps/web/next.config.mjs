/** @type {import('next').NextConfig} */
const nextConfig = {
  // Header "X-Powered-By: Next.js" membocorkan teknologi tanpa manfaat —
  // permukaan serangan yang tidak perlu untuk profil VPS self-managed.
  poweredByHeader: false,
  reactStrictMode: true,
  compiler: {
    // console.* dibuang hanya di build produksi; pengembangan tetap memunculkannya.
    // Log di klien tidak ada gunanya bagi pengguna akhir dan hanya mengisi bundle.
    removeConsole: process.env.NODE_ENV === "production" ? { exclude: ["error"] } : false,
  },
};

export default nextConfig;
