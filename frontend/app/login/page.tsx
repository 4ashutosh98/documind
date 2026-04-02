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
          Choose your account to continue
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
    </div>
  );
}
