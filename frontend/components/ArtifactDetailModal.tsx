"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getArtifact, reembedArtifact } from "@/lib/api";
import type { ArtifactDetail, ChunkResponse } from "@/types";

interface Props {
  artifactId: string;
  userId: string;
  onClose: () => void;
  onStatusChange: () => void; // parent refreshes artifact list when reembed is triggered
}

const TYPE_COLORS: Record<string, string> = {
  pdf: "#EF4444",
  docx: "#3B82F6",
  doc: "#3B82F6",
  xlsx: "#10B981",
  xls: "#10B981",
};

const CHUNK_TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  heading: { bg: "rgba(251,191,36,0.15)", text: "#FBBF24" },
  table_row: { bg: "rgba(16,185,129,0.15)", text: "#10B981" },
  text: { bg: "rgba(139,158,184,0.12)", text: "#8B9EB8" },
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatDate(ts: string): string {
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: "short", day: "numeric", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function ProvenanceBadge({ provenance, fileType }: { provenance: ChunkResponse["provenance"]; fileType: string }) {
  const parts: string[] = [];
  if (provenance.page != null) parts.push(`p.${provenance.page}`);
  // Full heading path: "Part I > Chapter 2 > Section 3"
  const fullSection = provenance.breadcrumb && provenance.section
    ? `${provenance.breadcrumb} > ${provenance.section}`
    : (provenance.section ?? null);
  if (fullSection) parts.push(fullSection);
  if (provenance.sheet) parts.push(`Sheet: ${provenance.sheet}`);
  if (provenance.row_start != null && provenance.row_end != null)
    parts.push(`Rows ${provenance.row_start}–${provenance.row_end}`);
  if (provenance.char_start != null && provenance.char_end != null && fileType !== "xlsx")
    parts.push(`chars ${provenance.char_start}–${provenance.char_end}`);
  if (!parts.length) return null;
  return (
    <span style={{ fontSize: 10, color: "#8B9EB8", opacity: 0.85 }}>
      {parts.join(" · ")}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    ready:   { label: "Fully indexed", color: "#10B981", bg: "rgba(16,185,129,0.12)" },
    pending: { label: "Indexing...",   color: "#F59E0B", bg: "rgba(245,158,11,0.12)" },
    none:    { label: "Not indexed",   color: "#8B9EB8", bg: "rgba(139,158,184,0.10)" },
  };
  const s = map[status] ?? map.none;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      background: s.bg, color: s.color,
      fontSize: 11, fontWeight: 600, padding: "3px 9px", borderRadius: 20,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%", background: s.color,
        ...(status === "pending" ? { animation: "pulse 1.5s ease-in-out infinite" } : {}),
      }} />
      {s.label}
    </span>
  );
}

function FeaturePill({ label, icon }: { label: string; icon: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      background: "rgba(123,179,240,0.10)", color: "#7BB3F0",
      fontSize: 10, fontWeight: 500, padding: "3px 8px", borderRadius: 20,
      border: "1px solid rgba(123,179,240,0.2)",
    }}>
      <span style={{ fontSize: 11 }}>{icon}</span>
      {label}
    </span>
  );
}

function MetaGrid({ rows }: { rows: { key: string; value: string }[] }) {
  if (!rows.length) return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 16px", alignItems: "baseline" }}>
      {rows.map(({ key, value }) => (
        <>
          <span key={`k-${key}`} style={{ fontSize: 11, color: "#8B9EB8", whiteSpace: "nowrap" }}>{key}</span>
          <span key={`v-${key}`} style={{ fontSize: 12, color: "#CBD5E1", wordBreak: "break-word", fontFamily: key === "Hash" ? "monospace" : undefined }}>{value}</span>
        </>
      ))}
    </div>
  );
}

function MetadataSection({ metadata, fileType }: { metadata: Record<string, unknown>; fileType: string }) {
  const rows: { key: string; value: string }[] = [];

  if (fileType === "xlsx") {
    const names = metadata.sheet_names as string[] | undefined;
    const counts = metadata.sheet_row_counts as Record<string, number> | undefined;
    if (names?.length) rows.push({ key: "Sheets", value: names.join(", ") });
    if (counts) {
      Object.entries(counts).forEach(([sheet, count]) =>
        rows.push({ key: `  ${sheet}`, value: `${count} rows` })
      );
    }
    if (metadata.total_rows != null)
      rows.push({ key: "Total rows", value: String(metadata.total_rows) });
  } else {
    if (metadata.title) rows.push({ key: "Title", value: String(metadata.title) });
    if (metadata.author) rows.push({ key: "Author", value: String(metadata.author) });
    if (metadata.page_count != null) rows.push({ key: "Pages", value: String(metadata.page_count) });
    if (metadata.section_count != null) rows.push({ key: "Sections", value: String(metadata.section_count) });
    const headings = metadata.headings as string[] | undefined;
    if (headings?.length) {
      rows.push({ key: "Headings", value: `${headings.length} found` });
      headings.slice(0, 10).forEach((h, i) =>
        rows.push({ key: `  ${i + 1}.`, value: h })
      );
      if (headings.length > 10)
        rows.push({ key: "", value: `…and ${headings.length - 10} more` });
    }
  }

  if (!rows.length) {
    return <p style={{ fontSize: 12, color: "#8B9EB8", fontStyle: "italic" }}>No metadata extracted.</p>;
  }

  return <MetaGrid rows={rows} />;
}

function SystemSection({ detail }: { detail: ArtifactDetail }) {
  const rows: { key: string; value: string }[] = [
    { key: "Uploaded by", value: detail.uploaded_by },
    { key: "Version",     value: `v${detail.version_number}${detail.parent_id ? " (has parent)" : ""}` },
    { key: "Hash",        value: `${detail.file_hash.slice(0, 16)}…` },
    { key: "First seen",  value: formatDate(detail.first_seen) },
    { key: "Last seen",   value: formatDate(detail.last_seen) },
  ];
  return <MetaGrid rows={rows} />;
}

export default function ArtifactDetailModal({ artifactId, userId, onClose, onStatusChange }: Props) {
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [reembedding, setReembedding] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [expandedChunkId, setExpandedChunkId] = useState<string | null>(null);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    setLoading(true);
    getArtifact(artifactId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [artifactId]);

  async function handleReembed() {
    if (!detail || reembedding || detail.embedding_status === "pending") return;
    setReembedding(true);
    try {
      await reembedArtifact(artifactId, userId);
      // Optimistically update local state so the button changes immediately
      setDetail((d) => d ? { ...d, embedding_status: "pending" } : d);
      onStatusChange();
    } catch {
      // ignore
    } finally {
      setReembedding(false);
    }
  }

  const totalTokens = detail?.chunks.reduce((s, c) => s + (c.token_count ?? 0), 0) ?? 0;

  if (!mounted) return null;

  return createPortal(
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 10000,
        background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          width: "100%", maxWidth: 700,
          maxHeight: "88vh",
          background: "#0B0F1C",
          border: "1px solid #1C2A40",
          borderRadius: 20,
          boxShadow: "0 24px 64px rgba(0,0,0,0.7)",
          display: "flex", flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* ── Header ──────────────────────────────────────────────────── */}
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 12,
          padding: "20px 24px 16px",
          borderBottom: "1px solid #1C2A40",
          flexShrink: 0,
        }}>
          {detail && (
            <div style={{
              padding: "3px 7px", borderRadius: 5, fontSize: 9,
              fontWeight: 700, letterSpacing: "0.06em", color: "white",
              background: TYPE_COLORS[detail.file_type] ?? "#7BB3F0",
              flexShrink: 0, marginTop: 2, textTransform: "uppercase",
            }}>
              {detail.file_type}
            </div>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: 15, fontWeight: 600, color: "#E2E8F0", lineHeight: 1.3, wordBreak: "break-word" }}>
              {detail?.filename ?? "Loading…"}
            </p>
            {detail && (
              <p style={{ fontSize: 11, color: "#8B9EB8", marginTop: 3 }}>
                {formatBytes(detail.size_bytes)}
                {detail.version_number > 1 && ` · v${detail.version_number}`}
                {` · Uploaded ${formatDate(detail.upload_timestamp)}`}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              flexShrink: 0, width: 28, height: 28, borderRadius: 8,
              background: "rgba(255,255,255,0.05)", border: "none",
              color: "#8B9EB8", cursor: "pointer", fontSize: 16,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            ×
          </button>
        </div>

        {/* ── Scrollable body ─────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
          {loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 120 }}>
              <div style={{
                width: 24, height: 24, borderRadius: "50%",
                border: "2px solid #1C2A40", borderTopColor: "#7BB3F0",
                animation: "spin 0.8s linear infinite",
              }} />
            </div>
          ) : !detail ? (
            <p style={{ color: "#EF4444", fontSize: 13 }}>Failed to load artifact details.</p>
          ) : (
            <>
              {/* Extracted metadata */}
              <section style={{ marginBottom: 24 }}>
                <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", color: "#4A90D9", textTransform: "uppercase", marginBottom: 10 }}>
                  File Metadata
                </p>
                <MetadataSection metadata={detail.extracted_metadata} fileType={detail.file_type} />
              </section>

              {/* System metadata */}
              <section style={{ marginBottom: 24 }}>
                <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", color: "#4A90D9", textTransform: "uppercase", marginBottom: 10 }}>
                  System Info
                </p>
                <SystemSection detail={detail} />
              </section>

              {/* Chunks */}
              <section>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 12 }}>
                  <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", color: "#4A90D9", textTransform: "uppercase" }}>
                    Chunks
                  </p>
                  <span style={{ fontSize: 11, color: "#8B9EB8" }}>
                    {detail.chunks.length} chunk{detail.chunks.length !== 1 ? "s" : ""}
                    {totalTokens > 0 && ` · ~${totalTokens.toLocaleString()} tokens`}
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {detail.chunks.map((chunk) => {
                    const typeStyle = CHUNK_TYPE_COLORS[chunk.chunk_type] ?? CHUNK_TYPE_COLORS.text;
                    const isExpanded = expandedChunkId === chunk.id;
                    return (
                      <div
                        key={chunk.id}
                        onClick={() => setExpandedChunkId(isExpanded ? null : chunk.id)}
                        style={{
                          background: isExpanded ? "#111827" : "#0D1526",
                          border: `1px solid ${isExpanded ? "#2D4A6A" : "#1C2A40"}`,
                          borderRadius: 10, padding: "10px 12px",
                          cursor: "pointer",
                          transition: "border-color 0.15s, background 0.15s",
                        }}
                      >
                        {/* Chunk header row */}
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                          <span style={{ fontSize: 10, color: "#4A5568", minWidth: 22 }}>
                            #{chunk.chunk_index + 1}
                          </span>
                          <span style={{
                            fontSize: 9, fontWeight: 600, textTransform: "uppercase",
                            letterSpacing: "0.06em", padding: "2px 6px", borderRadius: 4,
                            background: typeStyle.bg, color: typeStyle.text,
                          }}>
                            {chunk.chunk_type.replace("_", " ")}
                          </span>
                          {chunk.token_count != null && (
                            <span style={{ fontSize: 10, color: "#8B9EB8", marginLeft: "auto" }}>
                              {chunk.token_count} tok
                            </span>
                          )}
                          {/* Expand/collapse chevron */}
                          <svg
                            width="10" height="10" viewBox="0 0 10 10" fill="none"
                            style={{
                              flexShrink: 0,
                              transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                              transition: "transform 0.15s",
                              color: "#4A5568",
                            }}
                          >
                            <path d="M2 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </div>
                        {/* Provenance */}
                        <div style={{ marginBottom: 5 }}>
                          <ProvenanceBadge provenance={chunk.provenance} fileType={detail.file_type} />
                        </div>
                        {/* Text — truncated by default, full when expanded */}
                        <p style={{
                          fontSize: 11, color: "#94A3B8", lineHeight: 1.6,
                          fontFamily: "monospace", whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          ...(isExpanded ? {} : {
                            maxHeight: 80, overflow: "hidden",
                            maskImage: "linear-gradient(to bottom, black 60%, transparent 100%)",
                            WebkitMaskImage: "linear-gradient(to bottom, black 60%, transparent 100%)",
                          }),
                        }}>
                          {chunk.text}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </section>
            </>
          )}
        </div>

        {/* ── Footer: status + re-embed ─────────────────────────────── */}
        {detail && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 12, padding: "14px 24px",
            borderTop: "1px solid #1C2A40", flexShrink: 0,
            flexWrap: "wrap",
          }}>
            {/* Left: status + feature pills (only shown when status=ready and feature actually ran) */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <StatusBadge status={detail.embedding_status} />
              {(() => {
                if (detail.embedding_status !== "ready") return null;
                const f = detail.extracted_metadata?.indexed_features as
                  | { doc2query?: boolean; contextual_enrichment?: boolean; image_description?: boolean }
                  | undefined;
                // Legacy: no indexed_features key → show all pills (indexed before tracking was added)
                const legacy = f === undefined || f === null;
                return (
                  <>
                    {(legacy || f?.contextual_enrichment) && <FeaturePill label="Context" icon="✦" />}
                    {(legacy || f?.doc2query) && <FeaturePill label="Doc2Query" icon="❓" />}
                    {(legacy || f?.image_description) && <FeaturePill label="Vision" icon="👁" />}
                  </>
                );
              })()}
            </div>

            {/* Right: re-embed button */}
            <button
              onClick={handleReembed}
              disabled={reembedding || detail.embedding_status === "pending"}
              style={{
                display: "flex", alignItems: "center", gap: 7,
                padding: "8px 16px", borderRadius: 10, fontSize: 12, fontWeight: 600,
                border: "none", cursor: reembedding || detail.embedding_status === "pending" ? "not-allowed" : "pointer",
                background: detail.embedding_status === "pending"
                  ? "rgba(245,158,11,0.15)"
                  : "rgba(123,179,240,0.15)",
                color: detail.embedding_status === "pending" ? "#F59E0B" : "#7BB3F0",
                transition: "all 0.15s",
                opacity: reembedding ? 0.6 : 1,
              }}
            >
              {detail.embedding_status === "pending" ? (
                <>
                  <span style={{
                    width: 10, height: 10, borderRadius: "50%",
                    border: "1.5px solid currentColor", borderTopColor: "transparent",
                    animation: "spin 0.8s linear infinite", flexShrink: 0,
                  }} />
                  Indexing…
                </>
              ) : (
                <>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M10 6A4 4 0 1 1 6 2M6 2l2-2M6 2l2 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  {detail.embedding_status === "ready" ? "Re-index" : "Start indexing"}
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {/* Keyframe animations injected once */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
      `}</style>
    </div>,
    document.body
  );
}
