import type { NextConfig } from "next";
import os from "os";
import path from "path";

/**
 * This machine's addresses on the local network (e.g. 10.0.0.253), read each time the dev
 * server starts, so a phone on the same Wi-Fi can open http://<address>:3000. Next blocks dev
 * resources (live reload) for any other host. Development only; production ignores it.
 */
const lanAddresses = Object.values(os.networkInterfaces())
  .flat()
  .filter((a) => a !== undefined && a.family === "IPv4" && !a.internal)
  .map((a) => a!.address);

const nextConfig: NextConfig = {
  // Repository root (not js/): Vercel's Git builds resolve the app path
  // relative to this, so it must be the repo root for Root Directory = js.
  outputFileTracingRoot: path.join(__dirname, "..", "..", ".."),
  allowedDevOrigins: lanAddresses,
};

export default nextConfig;
