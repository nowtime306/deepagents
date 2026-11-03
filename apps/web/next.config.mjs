/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.PLATFORM_WEB_DIST_DIR || ".next",
  async rewrites() {
    const target = process.env.PLATFORM_API_URL || "http://127.0.0.1:8100";
    return [
      { source: "/api/v1/:path*", destination: `${target}/api/v1/:path*` },
      {
        source: "/api/public/v1/:path*",
        destination: `${target}/api/public/v1/:path*`,
      },
    ];
  },
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
};

export default nextConfig;
