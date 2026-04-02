"use client";

import { useState } from "react";
import type { MessageResponse } from "@/types";
import SourceCard from "./SourceCard";

interface Props {
  message: MessageResponse;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderContent(text: string) {
  return text.split("\n").map((line, i) => {
    // Bold **text**
    const parts = line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={j}>{part.slice(2, -2)}</strong>;
      }
      return part;
    });
    // Blockquote >
    if (line.startsWith("> ")) {
      return (
        <blockquote key={i} className="border-l-2 pl-3 py-0.5 my-1 rounded-r-lg text-sm" style={{ borderColor: "var(--primary-light)", background: "var(--sidebar-bg)", color: "var(--text-secondary)" }}>
          {line.slice(2)}
        </blockquote>
      );
    }
    if (!line.trim()) return <br key={i} />;
    return <p key={i} className="leading-relaxed">{parts}</p>;
  });
}

export default function AssistantMessage({ message }: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const results = message.query_results?.results ?? [];
  const hasResults = results.length > 0;

  return (
    <div className="flex justify-start">
      {/* Assistant avatar */}
      <div
        className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center mr-2.5 mt-0.5"
        style={{ background: "var(--primary)" }}
      >
        <svg width="13" height="13" viewBox="0 0 32 32" fill="none">
          <path d="M8 24V8l10 8-10 8z" fill="white" fillOpacity="0.9" />
          <path d="M18 20V12l6 4-6 4z" fill="white" fillOpacity="0.6" />
        </svg>
      </div>

      <div className="max-w-[80%] flex flex-col gap-2">
        {/* Main message card */}
        <div
          className="px-4 py-3 rounded-2xl rounded-bl-sm border text-sm"
          style={{
            background: "var(--assistant-bg)",
            borderColor: "var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          {renderContent(message.content)}
        </div>

        {/* Sources toggle */}
        {hasResults && (
          <div className="space-y-1.5">
            <button
              className="flex items-center gap-1.5 text-xs font-medium transition-colors duration-150"
              style={{ color: sourcesOpen ? "var(--primary-dark)" : "var(--text-muted)" }}
              onClick={() => setSourcesOpen(!sourcesOpen)}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                className="transition-transform duration-150"
                style={{ transform: sourcesOpen ? "rotate(180deg)" : "rotate(0)" }}
              >
                <path
                  d="M2 4l4 4 4-4"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
              {results.length} source{results.length !== 1 ? "s" : ""}
            </button>

            {sourcesOpen && (
              <div className="space-y-1.5">
                {results.map((match, i) => (
                  <SourceCard key={match.chunk.id} match={match} index={i + 1} />
                ))}
              </div>
            )}
          </div>
        )}

        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {formatTime(message.created_at)}
        </span>
      </div>
    </div>
  );
}
