import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: process.env.NEXT_OUTPUT_MODE === "export" ? "export" : "standalone",
  poweredByHeader: false,
};

export default nextConfig;
