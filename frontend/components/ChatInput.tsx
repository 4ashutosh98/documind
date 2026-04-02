"use client";

import { useState, useRef, KeyboardEvent } from "react";

interface Props {
  onSend: (text: string) => void;
  loading: boolean;
  disabled?: boolean;
  placeholder?: string;
  onUpload?: () => void;
}

export default function ChatInput({
  onSend,
  loading,
  disabled,
  placeholder = "Ask anything about your documents\u2026",
  onUpload,
}: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || loading || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function handleInput() {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }

  return (
    <div
      className="border-t px-4 py-4"
      style={{ borderColor: "var(--border)", background: "var(--surface)" }}
    >
      <div
        className="flex items-end gap-2 max-w-2xl mx-auto rounded-2xl border px-3 py-2.5 transition-all duration-200 focus-within:border-primary"
        style={{ borderColor: "var(--border)", background: "var(--bg)" }}
      >
        {/* Upload button */}
        {onUpload && (
          <button
            type="button"
            onClick={onUpload}
            title="Upload documents"
            className="shrink-0 mb-0.5 w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150"
            style={{ color: "var(--text-muted)", background: "var(--primary-light)" }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "var(--primary-dark)")}
            onMouseLeave={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "var(--text-muted)")}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path
                d="M6.5 2v9M2 6.5h9"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled || loading}
          placeholder={placeholder}
          className="flex-1 resize-none bg-transparent outline-none text-sm leading-relaxed placeholder:text-muted"
          style={{
            color: "var(--text-primary)",
            maxHeight: "160px",
            lineHeight: "1.5",
          }}
        />

        <button
          onClick={submit}
          disabled={!value.trim() || loading || disabled}
          className="shrink-0 mb-0.5 w-8 h-8 rounded-xl flex items-center justify-center transition-all duration-200 disabled:opacity-30"
          style={{ background: "var(--primary)" }}
        >
          {loading ? (
            <svg className="animate-spin" width="15" height="15" viewBox="0 0 15 15" fill="none">
              <circle cx="7.5" cy="7.5" r="5.5" stroke="white" strokeWidth="2" strokeDasharray="18 8" strokeLinecap="round" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <path
                d="M12 7.5H3M8.5 4l3.5 3.5L8.5 11"
                stroke="white"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </button>
      </div>

      <p className="text-center mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
        Enter to send &middot; Shift+Enter for new line
      </p>
    </div>
  );
}
