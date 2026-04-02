"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getUser } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function LandingPage() {
  const router = useRouter();
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    if (getUser()) router.replace("/chat");
  }, [router]);

  async function handleReset() {
    if (!confirm("This will permanently delete all artifacts, conversations, and embeddings. Are you sure?")) return;
    setResetting(true);
    try {
      await fetch(`${BASE}/dev/reset`, { method: "POST" });
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="animated-gradient min-h-screen flex items-center justify-center">
      <div className="text-center space-y-8 px-6">
        {/* Logo mark */}
        <div className="flex justify-center mb-2">
          <div
            className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-card"
            style={{ background: "var(--primary)" }}
          >
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <path
                d="M8 24V8l10 8-10 8z"
                fill="white"
                fillOpacity="0.9"
              />
              <path
                d="M18 20V12l6 4-6 4z"
                fill="white"
                fillOpacity="0.6"
              />
            </svg>
          </div>
        </div>

        {/* Wordmark */}
        <div>
          <h1
            className="text-6xl font-light tracking-tight"
            style={{ color: "var(--text-primary)", letterSpacing: "-0.03em" }}
          >
            DocuMind
          </h1>
          <p
            className="mt-3 text-lg font-light"
            style={{ color: "var(--text-secondary)" }}
          >
            Document intelligence, beautifully simple.
          </p>
        </div>

        {/* CTA */}
        <div className="flex flex-col items-center gap-3">
          <button
            onClick={() => router.push("/login")}
            className="inline-flex items-center gap-2 px-8 py-3.5 rounded-full font-medium text-white transition-all duration-200 hover:shadow-lift hover:-translate-y-0.5"
            style={{ background: "var(--primary)" }}
          >
            Sign In
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M3 8h10M9 4l4 4-4 4"
                stroke="white"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          <button
            onClick={handleReset}
            disabled={resetting}
            className="text-xs transition-opacity hover:opacity-70 disabled:opacity-40"
            style={{ color: "var(--text-muted)" }}
          >
            {resetting ? "Resetting…" : "🗑 Reset all data"}
          </button>
        </div>
      </div>
    </div>
  );
}
