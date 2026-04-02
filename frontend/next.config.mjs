/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: produces an `out/` directory of plain HTML/CSS/JS.
  // FastAPI serves this directory in the HF Spaces Docker container.
  // All pages use "use client" with no server-side data fetching, so nothing is lost.
  output: "export",

  // Required for static export: Next.js generates /chat/index.html instead of /chat.html.
  // FastAPI's catch-all route checks for <path>/index.html to handle direct navigation.
  trailingSlash: true,
};

export default nextConfig;
