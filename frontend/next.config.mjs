/** @type {import('next').NextConfig} */
const isStandalone = process.env.NEXT_OUTPUT_MODE === "standalone";
const disableBuildRewrites = process.env.NEXT_DISABLE_BUILD_REWRITES === "1";

const nextConfig = {
  reactStrictMode: false,
  ...(isStandalone ? { output: "standalone" } : {}),
  ...(!disableBuildRewrites
    ? {
        async rewrites() {
          const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";
          return [
            {
              source: "/api/:path*",
              destination: `${backendUrl}/api/:path*`,
            },
          ];
        },
      }
    : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
        ],
      },
    ];
  },
};

export default nextConfig;
