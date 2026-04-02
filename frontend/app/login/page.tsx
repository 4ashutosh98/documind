"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getUser, setUser } from "@/lib/auth";
import Avatar from "@/components/Avatar";

const USERS = [
  { id: "user1", label: "User 1" },
  { id: "user2", label: "User 2" },
  { id: "user3", label: "User 3" },
];

export default function LoginPage() {
  const router = useRouter();

  useEffect(() => {
    if (getUser()) router.replace("/chat");
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
    </div>
  );
}
