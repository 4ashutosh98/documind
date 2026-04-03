"use client";

import { useState, useEffect } from "react";

const GROQ_KEY = "documind_groq_key";
const GOOGLE_KEY = "documind_google_key";

interface Props {
  onClose: () => void;
}

function KeyInput({
  label,
  storageKey,
  placeholder,
  status,
  children,
}: {
  label: string;
  storageKey: string;
  placeholder: string;
  status: string;
  children: React.ReactNode;
}) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const [isSet, setIsSet] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(storageKey) ?? "";
    setValue(stored);
    setIsSet(!!stored);
  }, [storageKey]);

  function handleSave() {
    const trimmed = value.trim();
    if (trimmed) {
      localStorage.setItem(storageKey, trimmed);
      setIsSet(true);
    } else {
      localStorage.removeItem(storageKey);
      setIsSet(false);
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleClear() {
    localStorage.removeItem(storageKey);
    setValue("");
    setIsSet(false);
    setSaved(false);
  }

  return (
    <div
      className="rounded-xl border p-4 space-y-3"
      style={{ borderColor: "var(--border)", background: "var(--bg)" }}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          {label}
        </span>
        <span
          className="text-xs px-2 py-0.5 rounded-full"
          style={{
            background: isSet ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.1)",
            color: isSet ? "#22c55e" : "#ef4444",
          }}
        >
          {isSet ? "Key saved" : "Not set — using shared key"}
        </span>
      </div>

      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{status}</p>

      {/* Instructions */}
      <div className="text-xs space-y-0.5" style={{ color: "var(--text-muted)" }}>
        {children}
      </div>

      <input
        type="password"
        value={value}
        onChange={(e) => { setValue(e.target.value); setSaved(false); }}
        placeholder={placeholder}
        className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
        style={{
          background: "var(--surface)",
          borderColor: "var(--border)",
          color: "var(--text-primary)",
        }}
      />

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          className="flex-1 rounded-lg py-1.5 text-sm font-medium transition-opacity hover:opacity-80"
          style={{ background: "var(--primary)", color: "#fff" }}
        >
          {saved ? "Saved!" : "Save"}
        </button>
        <button
          onClick={handleClear}
          className="rounded-lg px-3 py-1.5 text-sm border transition-opacity hover:opacity-70"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          Clear
        </button>
      </div>
    </div>
  );
}

export default function ApiKeysModal({ onClose }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-lg rounded-2xl border shadow-xl flex flex-col"
        style={{ background: "var(--surface)", borderColor: "var(--border)", maxHeight: "90vh" }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b shrink-0"
          style={{ borderColor: "var(--border)" }}
        >
          <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
            API Keys
          </h2>
          <button
            onClick={onClose}
            className="text-sm px-2 py-1 rounded hover:opacity-70 transition-opacity"
            style={{ color: "var(--text-muted)" }}
          >
            ✕
          </button>
        </div>

        <div className="px-6 py-5 space-y-4 overflow-y-auto">
          {/* Why this matters */}
          <div
            className="rounded-xl border px-4 py-3 text-xs leading-relaxed space-y-1.5"
            style={{ background: "rgba(59,130,246,0.06)", borderColor: "rgba(59,130,246,0.2)", color: "var(--text-muted)" }}
          >
            <p className="font-semibold" style={{ color: "var(--primary)" }}>
              This is a portfolio demo — please use your own API keys
            </p>
            <p>
              DocuMind uses Groq for AI answers and Google Gemini for document embeddings.
              Both have free tiers, but shared demo keys have limited daily quotas.
              Adding your own keys means you use your own quota — the demo keeps working
              for everyone else too.
            </p>
          </div>

          {/* Rate limits table */}
          <div>
            <p className="text-xs font-semibold mb-2" style={{ color: "var(--text-secondary)" }}>
              Free-tier rate limits
            </p>
            <div className="rounded-xl border overflow-hidden text-xs" style={{ borderColor: "var(--border)" }}>
              <table className="w-full">
                <thead>
                  <tr style={{ background: "var(--bg)" }}>
                    <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--text-muted)" }}>Service</th>
                    <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--text-muted)" }}>Used for</th>
                    <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--text-muted)" }}>Free limit</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>Groq</td>
                    <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>AI answers, enrichment, doc2query</td>
                    <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>14,400 req/day · 30 RPM</td>
                  </tr>
                  <tr className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>Google Gemini</td>
                    <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>Document & query embeddings</td>
                    <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>1,500 req/day</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Groq key */}
          <KeyInput
            label="Groq API Key"
            storageKey={GROQ_KEY}
            placeholder="gsk_..."
            status="Used for: AI chat answers · contextual chunk enrichment · doc2query question generation · query rewriting"
          >
            <ol className="list-decimal list-inside space-y-0.5">
              <li>Go to <a href="https://console.groq.com" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: "var(--primary)" }}>console.groq.com</a></li>
              <li>Sign up with Google or email — no credit card required</li>
              <li>Click <strong style={{ color: "var(--text-secondary)" }}>API Keys</strong> in the left sidebar</li>
              <li>Click <strong style={{ color: "var(--text-secondary)" }}>Create API Key</strong> → give it a name → copy it</li>
              <li>Paste it below — starts with <code style={{ color: "var(--text-secondary)" }}>gsk_</code></li>
            </ol>
          </KeyInput>

          {/* Google Gemini key */}
          <KeyInput
            label="Google Gemini API Key"
            storageKey={GOOGLE_KEY}
            placeholder="AIza..."
            status="Used for: embedding documents and queries into 768-dim vectors for semantic search"
          >
            <ol className="list-decimal list-inside space-y-0.5">
              <li>Go to <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: "var(--primary)" }}>aistudio.google.com/app/apikey</a></li>
              <li>Sign in with your Google account</li>
              <li>Click <strong style={{ color: "var(--text-secondary)" }}>Create API key</strong></li>
              <li>Select <strong style={{ color: "var(--text-secondary)" }}>Create API key in new project</strong> (or pick an existing project)</li>
              <li>Copy the key — starts with <code style={{ color: "var(--text-secondary)" }}>AIza</code></li>
              <li>No billing setup required for the free tier (1,500 embedding requests/day)</li>
            </ol>
          </KeyInput>

          <p className="text-xs text-center pb-1" style={{ color: "var(--text-muted)" }}>
            Keys are stored in your browser only — never sent to anyone except the respective API provider.
          </p>
        </div>
      </div>
    </div>
  );
}
