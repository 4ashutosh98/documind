"use client";

import { useState } from "react";
import type { QueryMatch } from "@/types";
import HighlightedText from "./HighlightedText";

const TYPE_COLORS: Record<string, string> = {
  pdf:  "#EF4444",
  docx: "#3B82F6",
  xlsx: "#10B981",
};

const SEARCH_TYPE_STYLES: Record<string, { label: string; bg: string; color: string }> = {
  keyword:  { label: "FTS",      bg: "#F1F5F9", color: "#64748B" },
  semantic: { label: "semantic", bg: "#EDE9FE", color: "#7C3AED" },
  hybrid:   { label: "hybrid",   bg: "#DCFCE7", color: "#16A34A" },
};

interface Props {
  match: QueryMatch;
  index: number;
}

function provenanceLabel(match: QueryMatch): string {
  const p = match.chunk.provenance;
  const parts: string[] = [];
  if (p.page != null) parts.push(`p.${p.page}`);
  // Build full heading path: "Part I > Chapter 2 > Section 3"
  const fullSection = p.breadcrumb && p.section
    ? `${p.breadcrumb} > ${p.section}`
    : (p.section ?? null);
  if (fullSection) parts.push(fullSection.length > 55 ? fullSection.slice(0, 55) + "…" : fullSection);
  if (p.sheet) parts.push(p.sheet);
  if (p.row_start != null) parts.push(`rows ${p.row_start}–${p.row_end}`);
  return parts.join(" · ") || "—";
}

export default function SourceCard({ match, index }: Props) {
  const [expanded, setExpanded] = useState(false);
  const ext = match.artifact.file_type.toLowerCase();
  const badgeColor = TYPE_COLORS[ext] ?? "var(--primary)";
  const prov = provenanceLabel(match);
  const searchStyle = match.search_type ? SEARCH_TYPE_STYLES[match.search_type] : null;
  const preview = match.chunk.text.slice(0, 180);
  const hasMore = match.chunk.text.length > 180;

  return (
    <div
      className="rounded-xl border overflow-hidden transition-all duration-200"
      style={{ borderColor: "var(--border)", background: "var(--sidebar-bg)" }}
    >
      {/* Header row */}
      <button
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:opacity-80 transition-opacity"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-xs font-semibold" style={{ color: "var(--text-muted)" }}>
          {index}
        </span>

        <span
          className="shrink-0 px-1.5 py-0.5 rounded text-white font-bold uppercase"
          style={{ background: badgeColor, fontSize: "9px" }}
        >
          {ext}
        </span>

        <span
          className="flex-1 text-xs font-medium truncate"
          style={{ color: "var(--text-primary)" }}
        >
          {match.artifact.filename}
        </span>

        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {prov}
        </span>

        {searchStyle && (
          <span
            className="shrink-0 px-1.5 py-0.5 rounded-full font-medium"
            style={{ background: searchStyle.bg, color: searchStyle.color, fontSize: "9px" }}
          >
            {searchStyle.label}
          </span>
        )}

        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className="shrink-0 transition-transform duration-150"
          style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}
        >
          <path
            d="M2 4l4 4 4-4"
            stroke="var(--text-muted)"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* Collapsible chunk text */}
      {expanded && (
        <div className="px-3 pb-3 pt-0">
          <div
            className="text-xs leading-relaxed rounded-lg p-2.5"
            style={{
              background: "var(--surface)",
              color: "var(--text-secondary)",
              border: `1px solid var(--border)`,
            }}
          >
            <HighlightedText
              text={hasMore && !expanded ? preview + "…" : match.chunk.text}
              positions={match.match_positions as [number, number][]}
            />
          </div>
          {match.score != null && (
            <p className="mt-1.5 text-right text-xs" style={{ color: "var(--text-muted)" }}>
              score: {match.score.toFixed(3)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
