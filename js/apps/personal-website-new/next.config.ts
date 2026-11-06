import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Repository root (not js/): Vercel's Git builds resolve the app path
  // relative to this, so it must be the repo root for Root Directory = js.
  outputFileTracingRoot: path.join(__dirname, "..", "..", ".."),
};

export default nextConfig;