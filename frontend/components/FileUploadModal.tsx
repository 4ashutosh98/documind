"use client";

import { useState, useRef, DragEvent } from "react";

interface Props {
  onFilesSelected: (files: File[]) => void;
  onClose: () => void;
}

const ACCEPT = ".pdf,.docx,.xlsx";
const VALID_EXT = /\.(pdf|docx|xlsx)$/i;

export default function FileUploadModal({ onFilesSelected, onClose }: Props) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFiles(rawFiles: File[]) {
    const valid = rawFiles.filter((f) => VALID_EXT.test(f.name));
    if (valid.length === 0) {
      setError("Only PDF, DOCX, and XLSX files are supported.");
      return;
    }
    onFilesSelected(valid);
    onClose();
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    handleFiles(Array.from(e.dataTransfer.files));
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(30,41,59,0.4)", backdropFilter: "blur(4px)" }}
    >
      <div
        className="w-full max-w-md mx-4 rounded-2xl border p-6"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
            Upload documents
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.background = "var(--border)")
            }
            onMouseLeave={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.background = "transparent")
            }
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="var(--text-muted)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* Drop zone */}
        <div
          className="rounded-xl border-2 border-dashed flex flex-col items-center justify-center gap-3 p-10 cursor-pointer transition-all duration-150"
          style={{
            borderColor: dragging ? "var(--primary)" : "var(--border)",
            background: dragging ? "var(--sidebar-bg)" : "var(--bg)",
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
        >
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <path
              d="M16 22V10M10 16l6-6 6 6"
              stroke="var(--primary)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <rect x="4" y="24" width="24" height="2" rx="1" fill="var(--primary-light)" />
          </svg>
          <div className="text-center">
            <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
              Drop files here
            </p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
              or click to browse &middot; PDF, DOCX, XLSX &middot; multiple files supported
            </p>
          </div>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) handleFiles(files);
            e.target.value = "";
          }}
        />

        {error && (
          <p className="mt-3 text-sm text-center text-red-500">{error}</p>
        )}
      </div>
    </div>
  );
}
