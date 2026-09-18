import type { NextConfig } from "next";

/**
 * The dashboard shells out to the Python engine in ../engine, so every route
 * that touches it is dynamic. Nothing here is prerendered at build time: a
 * static snapshot of a rate forecast is a stale rate forecast.
 */
const config: NextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: __dirname,
};

export default config;
