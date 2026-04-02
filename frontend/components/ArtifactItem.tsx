"use client";

import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import type { ArtifactSummary } from "@/types";

const TYPE_COLORS: Record<string, string> = {
  pdf:  "#EF4444",
  docx: "#3B82F6",
  doc:  "#3B82F6",
  xlsx: "#10B981",
  xls:  "#10B981",
};

interface Props {
  artifact: ArtifactSummary;
  onDelete: () => void;
  onClick?: () => void;
  uploadProgress?: number; // defined (0–100) while HTTP upload is in-flight
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

// Only these three states get a visible icon + tooltip.
// "none" → no icon, no tooltip (file is already keyword-searchable, no need to signal anything).
type VisibleStatus = "uploading" | "pending" | "ready";

const STATUS_META: Record<VisibleStatus, { label: string; tooltip: string; color: string }> = {
  uploading: {
    label: "Uploading",
    tooltip: "Your file is being uploaded. Keyword search will be ready as soon as the upload finishes.",
    color: "#3B82F6",
  },
  pending: {
    label: "Building search index",
    tooltip:
      "Keyword search is active right now. Semantic indexing is running in the background \u2014 once done, natural-language queries like \u201csummarise this file\u201d will also work.",
    color: "#F59E0B",
  },
  ready: {
    label: "All search features active",
    tooltip:
      "Fully indexed. Hybrid search combines keyword matching with semantic similarity for the best results.",
    color: "#10B981",
  },
};

function StatusIcon({ status, uploadProgress }: { status: VisibleStatus | null; uploadProgress?: number }) {
  if (!status) {
    // "none" — render an invisible placeholder so layout stays stable
    return <div style={{ width: 14, height: 14 }} />;
  }

  if (status === "uploading") {
    const r = 5;
    const circ = 2 * Math.PI * r;
    // Keep at least a 10% arc so the ring is always visible
    const visiblePct = Math.max(uploadProgress ?? 0, 10);
    const offset = circ * (1 - visiblePct / 100);
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        {/* Track */}
        <circle cx="7" cy="7" r={r} stroke="#1C2A40" strokeWidth="2" />
        {/* Progress arc */}
        <circle
          cx="7" cy="7" r={r}
          stroke="#3B82F6" strokeWidth="2"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 7 7)"
        />
      </svg>
    );
  }

  if (status === "pending") {
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        {/* Warning triangle */}
        <path
          d="M7 1.5L12.8 12H1.2L7 1.5Z"
          fill="#F59E0B"
          fillOpacity="0.3"
          stroke="#F59E0B"
          strokeWidth="1.2"
          strokeLinejoin="round"
        />
        {/* Exclamation stem */}
        <line x1="7" y1="5.5" x2="7" y2="8.5" stroke="#F59E0B" strokeWidth="1.4" strokeLinecap="round" />
        {/* Exclamation dot */}
        <circle cx="7" cy="10.2" r="0.75" fill="#F59E0B" />
      </svg>
    );
  }

  // ready
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="6.5" fill="#10B981" fillOpacity="0.2" stroke="#10B981" strokeWidth="1.2" />
      <path d="M4.5 7l2 2 3-3" stroke="#10B981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function ArtifactItem({ artifact, onDelete, onClick, uploadProgress }: Props) {
  const ext = artifact.file_type.toLowerCase();
  const badgeColor = TYPE_COLORS[ext] ?? "var(--primary)";

  // Derive which visible status to show (null = "none", no icon)
  let visibleStatus: VisibleStatus | null = null;
  if (uploadProgress !== undefined) {
    visibleStatus = "uploading";
  } else if (artifact.embedding_status === "pending") {
    visibleStatus = "pending";
  } else if (artifact.embedding_status === "ready") {
    visibleStatus = "ready";
  }

  const meta = visibleStatus ? STATUS_META[visibleStatus] : null;

  // Portal-based tooltip (bypasses overflow:auto clipping in the sidebar)
  const iconRef = useRef<HTMLDivElement>(null);
  const [tipRect, setTipRect] = useState<DOMRect | null>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  function showTip() {
    if (meta && iconRef.current) setTipRect(iconRef.current.getBoundingClientRect());
  }
  function hideTip() { setTipRect(null); }

  return (
    <div
      className="group flex items-center gap-2.5 px-3 py-2.5 rounded-xl transition-all duration-150"
      style={{ background: "transparent", cursor: onClick && uploadProgress === undefined ? "pointer" : "default" }}
      onMouseEnter={(e) => ((e.currentTarget as HTMLDivElement).style.background = "var(--border)")}
      onMouseLeave={(e) => ((e.currentTarget as HTMLDivElement).style.background = "transparent")}
      onClick={uploadProgress === undefined ? onClick : undefined}
    >
      {/* File type badge */}
      <div
        className="shrink-0 px-1.5 py-0.5 rounded font-bold uppercase text-white"
        style={{ background: badgeColor, fontSize: "9px", letterSpacing: "0.05em" }}
      >
        {ext}
      </div>

      {/* Filename + size */}
      <div className="flex-1 min-w-0">
        <p
          className="text-xs font-medium truncate"
          style={{ color: "var(--text-primary)" }}
          title={artifact.filename}
        >
          {artifact.filename}
        </p>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {formatBytes(artifact.size_bytes)}
          {artifact.version_number > 1 && ` · v${artifact.version_number}`}
        </p>
      </div>

      {/* Status icon — invisible placeholder for "none", real icon for uploading/pending/ready */}
      <div
        ref={iconRef}
        className="shrink-0 cursor-default"
        onMouseEnter={showTip}
        onMouseLeave={hideTip}
      >
        <StatusIcon status={visibleStatus} uploadProgress={uploadProgress} />
      </div>

      {/* Delete button — hidden while uploading */}
      <button
        className={`${uploadProgress !== undefined ? "invisible" : "opacity-0 group-hover:opacity-100"} p-1 rounded-lg transition-all duration-150`}
        style={{ color: "var(--text-muted)" }}
        onMouseEnter={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "#EF4444")}
        onMouseLeave={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "var(--text-muted)")}
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="Delete file"
      >
        <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
          <path
            d="M2 3.5h10M5.5 3.5V2.5a.5.5 0 0 1 .5-.5h2a.5.5 0 0 1 .5.5v1M3.5 3.5l.5 8h6l.5-8"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {/* Portal tooltip — only shown when there is an active status */}
      {mounted && tipRect && meta && createPortal(
        <div
          style={{
            position: "fixed",
            top: tipRect.top + tipRect.height / 2,
            right: window.innerWidth - tipRect.left + 10,
            transform: "translateY(-50%)",
            zIndex: 9999,
            width: 228,
            background: "#0B0F1C",
            border: "1px solid #1C2A40",
            borderRadius: 12,
            padding: "10px 12px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
            pointerEvents: "none",
          }}
        >
          <p style={{ fontSize: 11, fontWeight: 600, color: meta.color, marginBottom: 4 }}>
            {meta.label}
          </p>
          <p style={{ fontSize: 11, color: "#8B9EB8", lineHeight: 1.5 }}>
            {meta.tooltip}
          </p>
          {/* Right-pointing caret (outer border) */}
          <div
            style={{
              position: "absolute",
              left: "100%",
              top: "50%",
              transform: "translateY(-50%)",
              borderWidth: 6,
              borderStyle: "solid",
              borderColor: "transparent transparent transparent #1C2A40",
            }}
          />
          {/* Right-pointing caret (inner fill) */}
          <div
            style={{
              position: "absolute",
              left: "calc(100% - 1px)",
              top: "50%",
              transform: "translateY(-50%)",
              borderWidth: 5,
              borderStyle: "solid",
              borderColor: "transparent transparent transparent #0B0F1C",
            }}
          />
        </div>,
        document.body
      )}
    </div>
  );
}
