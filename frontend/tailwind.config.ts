import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "sidebar-bg": "var(--sidebar-bg)",
        primary: "var(--primary)",
        "primary-light": "var(--primary-light)",
        "primary-dark": "var(--primary-dark)",
        border: "var(--border)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-muted": "var(--text-muted)",
        highlight: "var(--highlight)",
        "user-bubble": "var(--user-bubble)",
      },
      boxShadow: {
        soft: "0 2px 12px rgba(0,0,0,0.45)",
        card: "0 4px 20px rgba(0,0,0,0.55)",
        lift: "0 8px 32px rgba(0,0,0,0.65)",
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
    },
  },
  plugins: [],
};

export default config;
