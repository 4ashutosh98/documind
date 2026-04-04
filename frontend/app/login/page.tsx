"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getUser, setUser } from "@/lib/auth";
import { getApiBaseDiagnostic, warnIfApiBaseLooksMisconfigured } from "@/lib/api-base";
import Avatar from "@/components/Avatar";
import ApiKeysModal from "@/components/ApiKeysModal";

const USERS = [
  { id: "user1", label: "User 1" },
  { id: "user2", label: "User 2" },
  { id: "user3", label: "User 3" },
];

const LIVE_DEMO_URL = "https://ashutoshchoudhari-documind.hf.space";
const HF_SPACE_URL = "https://huggingface.co/spaces/ashutoshchoudhari/documind";
const GITHUB_URL = "https://github.com/4ashutosh98/documind";

export default function LoginPage() {
  const router = useRouter();
  const [showApiKeys, setShowApiKeys] = useState(false);
  const [apiDiagnostic, setApiDiagnostic] = useState<string | null>(null);

  useEffect(() => {
    if (getUser()) router.replace("/chat");
    const diagnostic = warnIfApiBaseLooksMisconfigured() ?? getApiBaseDiagnostic();
    if (diagnostic) setApiDiagnostic(diagnostic);
  }, [router]);

  function handleSelect(userId: string) {
    setUser(userId);
    router.push("/chat");
  }

  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center px-6"
      style={{ background: "var(--bg)" }}
    >
      <div className="text-center mb-12">
        <h1
          className="text-4xl font-light tracking-tight mb-2"
          style={{ color: "var(--text-primary)", letterSpacing: "-0.03em" }}
        >
          DocuMind
        </h1>
        <p style={{ color: "var(--text-muted)" }} className="text-sm">
          Choose an account to explore the demo
        </p>
      </div>

      <div className="flex gap-6">
        {USERS.map((u) => (
          <button
            key={u.id}
            onClick={() => handleSelect(u.id)}
            className="group flex flex-col items-center gap-3 p-6 rounded-2xl border transition-all duration-200 hover:shadow-lift hover:-translate-y-1 focus:outline-none"
            style={{
              background: "var(--surface)",
              borderColor: "var(--border)",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--primary)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--border)";
            }}
          >
            <Avatar userId={u.id} size={64} />
            <span
              className="text-sm font-medium"
              style={{ color: "var(--text-secondary)" }}
            >
              {u.label}
            </span>
          </button>
        ))}
      </div>

      {/* Mock auth notice */}
      <div
        className="mt-10 max-w-md w-full rounded-xl border px-5 py-4"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        <div className="flex items-start gap-3">
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            className="shrink-0 mt-0.5"
          >
            <circle cx="8" cy="8" r="7" stroke="#3B82F6" strokeWidth="1.5" />
            <path
              d="M8 7v4M8 5.5v.5"
              stroke="#3B82F6"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
          <div className="space-y-1">
            <p
              className="text-xs font-semibold tracking-wide uppercase"
              style={{ color: "var(--primary-dark)" }}
            >
              Demo / Portfolio project
            </p>
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              Authentication is simulated — there are no passwords or real accounts.
              All three user slots are open to anyone visiting this demo.
              Each slot has its own isolated document namespace so you can
              experiment without affecting others.
            </p>
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              All uploaded files, conversations, and embeddings are wiped
              automatically after a period of inactivity.
            </p>
          </div>
        </div>
      </div>

      {/* API keys button */}
      <button
        onClick={() => setShowApiKeys(true)}
        className="mt-4 text-xs underline hover:opacity-70 transition-opacity"
        style={{ color: "var(--text-muted)" }}
      >
        Use your own API keys
      </button>

      <div
        className="mt-5 max-w-md w-full rounded-xl border px-5 py-4"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        {apiDiagnostic && (
          <div
            className="mb-4 rounded-xl border px-4 py-3 text-xs leading-relaxed"
            style={{ background: "#2A1117", borderColor: "#7F1D1D", color: "#FECACA" }}
          >
            {apiDiagnostic}
          </div>
        )}

        <p
          className="text-xs font-semibold tracking-wide uppercase"
          style={{ color: "var(--primary-dark)" }}
        >
          Project links
        </p>
        <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Use the direct live demo link for the most reliable app experience.
          The Hugging Face Space page is best for inspecting the project,
          build details, and source context. The GitHub button opens the
          repository directly.
        </p>

        <div className="mt-4 flex flex-col gap-3">
          <a
            href={LIVE_DEMO_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition-all duration-200 hover:opacity-90"
            style={{ background: "var(--primary)", color: "white" }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M5 12h14M13 5l7 7-7 7"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Open Live Demo
          </a>

          <div className="flex flex-col gap-3 sm:flex-row">
            <a
              href={HF_SPACE_URL}
              target="_blank"
              rel="noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-all duration-200 hover:opacity-90"
              style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M4 6h16M4 12h16M4 18h16"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
              View Space Page / Source
            </a>

            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-all duration-200 hover:opacity-90"
              style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.3-1.3-1.7-1.3-1.7-1.1-.8.1-.8.1-.8 1.2.1 1.9 1.3 1.9 1.3 1.1 1.9 2.8 1.4 3.5 1.1.1-.8.4-1.4.8-1.7-2.7-.3-5.6-1.4-5.6-6.1 0-1.3.4-2.3 1.2-3.2-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.7 1.6.3 2.8.1 3.1.8.9 1.2 1.9 1.2 3.2 0 4.8-2.9 5.8-5.7 6.1.4.4.8 1.1.8 2.3v3.4c0 .3.2.7.8.6A12 12 0 0 0 12 .5z" />
              </svg>
              View on GitHub
            </a>
          </div>
        </div>
      </div>

      {showApiKeys && <ApiKeysModal onClose={() => setShowApiKeys(false)} />}
    </div>
  );
}
