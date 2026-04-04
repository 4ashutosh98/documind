"use client";

import { useEffect, useRef, useState, useCallback, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getUser, clearUser } from "@/lib/auth";
import {
  createConversation,
  deleteConversation,
  deleteArtifact,
  getMessages,
  listArtifacts,
  listConversations,
  sendMessage,
  streamArtifactStatus,
  uploadFile,
} from "@/lib/api";
import type {
  ArtifactSummary,
  ConversationSummary,
  MessageResponse,
} from "@/types";
import Avatar from "@/components/Avatar";
import ConversationItem from "@/components/ConversationItem";
import ArtifactItem from "@/components/ArtifactItem";
import ChatInput from "@/components/ChatInput";
import UserMessage from "@/components/UserMessage";
import AssistantMessage from "@/components/AssistantMessage";
import FileUploadModal from "@/components/FileUploadModal";
import ArtifactDetailModal from "@/components/ArtifactDetailModal";

const HF_SPACE_PAGE_URL =
  process.env.NEXT_PUBLIC_HF_SPACE_PAGE_URL ??
  "https://huggingface.co/spaces/ashutoshchoudhari/documind";

function describeError(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

// ---------------------------------------------------------------------------
// Inner component — uses useSearchParams (must be wrapped in Suspense)
// ---------------------------------------------------------------------------
function ChatInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [userId, setUserId] = useState<string | null>(null);
  const [uiError, setUiError] = useState<string | null>(null);

  // Sidebar state
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [detailArtifactId, setDetailArtifactId] = useState<string | null>(null);
  const [showHfSpaceButton, setShowHfSpaceButton] = useState(false);

  // In-flight uploads: tempId → { filename, file_type, size_bytes, progress }
  const [uploadingFiles, setUploadingFiles] = useState<
    { tempId: string; filename: string; file_type: string; size_bytes: number; progress: number }[]
  >([]);

  // Chat state
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageResponse[]>([]);
  const [sending, setSending] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  // ---------------------------------------------------------------------------
  // Auth guard
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const u = getUser();
    if (!u) {
      router.replace("/login");
    } else {
      setUserId(u);
    }
    if (typeof window !== "undefined") {
      setShowHfSpaceButton(window.location.hostname.endsWith(".hf.space"));
    }
  }, [router]);

  // ---------------------------------------------------------------------------
  // Load conversations + artifacts on mount / user change
  // ---------------------------------------------------------------------------
  const refreshConversations = useCallback(async (uid: string) => {
    try {
      const convs = await listConversations(uid);
      setConversations(convs);
    } catch (error) {
      console.error("Failed to load conversations", error);
      setUiError(describeError(error, "Could not load conversations from the backend."));
    }
  }, []);

  const refreshArtifacts = useCallback(async (uid: string) => {
    try {
      const arts = await listArtifacts(uid);
      setArtifacts(arts);
    } catch (error) {
      console.error("Failed to load artifacts", error);
      setUiError(describeError(error, "Could not load uploaded files from the backend."));
    }
  }, []);

  useEffect(() => {
    if (!userId) return;
    refreshConversations(userId);
    refreshArtifacts(userId);
  }, [userId, refreshConversations, refreshArtifacts]);

  // ---------------------------------------------------------------------------
  // SSE: open a stream while any artifact is pending; close when all done.
  // Replaces the old 5-second polling interval — the server pushes updates
  // at 1-second resolution and closes the connection when indexing finishes.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!userId) return;
    const hasPending = artifacts.some((a) => a.embedding_status === "pending");
    if (hasPending && !sseRef.current) {
      sseRef.current = streamArtifactStatus(
        userId,
        (updated) => setArtifacts(updated),
        () => { sseRef.current = null; }
      );
    } else if (!hasPending && sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
  }, [artifacts, userId]);

  // Close SSE connection on unmount
  useEffect(() => {
    return () => {
      if (sseRef.current) { sseRef.current.close(); sseRef.current = null; }
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Sync active conversation from URL query param ?cid=
  // ---------------------------------------------------------------------------
  const cidFromUrl = searchParams.get("cid");

  useEffect(() => {
    if (cidFromUrl && cidFromUrl !== activeConvId) {
      setActiveConvId(cidFromUrl);
    }
  }, [cidFromUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------------------
  // Load messages when active conversation changes
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!activeConvId || !userId) {
      setMessages([]);
      return;
    }
    getMessages(activeConvId, userId)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [activeConvId, userId]);

  // ---------------------------------------------------------------------------
  // Auto-scroll
  // ---------------------------------------------------------------------------
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------
  async function handleNewChat() {
    if (!userId) return;
    setUiError(null);
    try {
      const conv = await createConversation(userId);
      await refreshConversations(userId);
      selectConversation(conv.id);
    } catch (error) {
      console.error("Failed to create conversation", error);
      setUiError(describeError(error, "Could not start a new conversation."));
    }
  }

  function selectConversation(convId: string) {
    setActiveConvId(convId);
    router.push(`/chat?cid=${convId}`, { scroll: false });
  }

  async function handleDeleteConversation(convId: string) {
    if (!userId) return;
    try {
      await deleteConversation(convId, userId);
      if (activeConvId === convId) {
        setActiveConvId(null);
        setMessages([]);
        router.push("/chat", { scroll: false });
      }
      await refreshConversations(userId);
    } catch {
      // ignore
    }
  }

  async function handleDeleteArtifact(artifactId: string) {
    if (!userId) return;
    try {
      await deleteArtifact(artifactId, userId);
      await refreshArtifacts(userId);
    } catch {
      // ignore
    }
  }

  async function handleFilesSelected(files: File[]) {
    if (!userId) return;
    const uid = userId;
    setUiError(null);

    // Add phantom entries to sidebar immediately
    const phantoms = files.map((f) => ({
      tempId: Math.random().toString(36).slice(2),
      filename: f.name,
      file_type: f.name.split(".").pop()?.toLowerCase() ?? "pdf",
      size_bytes: f.size,
      progress: 0,
    }));
    setUploadingFiles((prev) => [...prev, ...phantoms]);

    // Upload files sequentially, updating progress in sidebar
    for (let i = 0; i < files.length; i++) {
      const { tempId } = phantoms[i];
      try {
        await uploadFile(files[i], uid, (pct) => {
          setUploadingFiles((prev) =>
            prev.map((e) => e.tempId === tempId ? { ...e, progress: pct } : e)
          );
        });
      } catch (error) {
        console.error(`Upload failed for ${files[i].name}`, error);
        setUiError(
          `Upload failed for ${files[i].name}: ${describeError(
            error,
            "The request failed before the server returned a response."
          )}`
        );
      }
      // Remove phantom entry and refresh so the real artifact appears
      setUploadingFiles((prev) => prev.filter((e) => e.tempId !== tempId));
      await refreshArtifacts(uid);
    }
  }

  async function handleSend(text: string) {
    if (!userId || !activeConvId || sending) return;
    setSending(true);

    // Show user message immediately before waiting for the API
    const tempId = `temp-${Date.now()}`;
    const optimisticMsg: MessageResponse = {
      id: tempId,
      conversation_id: activeConvId,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticMsg]);

    try {
      const resp = await sendMessage(activeConvId, userId, text);
      // Replace optimistic message with real one, then add assistant reply
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tempId),
        resp.user_message,
        resp.assistant_message,
      ]);
      await refreshConversations(userId);
    } catch {
      // Remove optimistic message on failure
      setMessages((prev) => prev.filter((m) => m.id !== tempId));
    } finally {
      setSending(false);
    }
  }

  function handleSignOut() {
    clearUser();
    router.replace("/");
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  if (!userId) return null;

  const userLabel = `User ${userId.replace(/\D/g, "")}`;

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--bg)" }}>
      {uiError && (
        <div className="fixed top-4 left-1/2 z-50 w-[min(720px,calc(100vw-2rem))] -translate-x-1/2 px-4">
          <div
            className="flex items-start gap-3 rounded-2xl border px-4 py-3 shadow-card"
            style={{ background: "#2A1117", borderColor: "#7F1D1D", color: "#FECACA" }}
          >
            <div className="mt-0.5 shrink-0">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.4" />
                <path d="M8 4.5v4M8 11.2v.3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </div>
            <p className="flex-1 text-sm leading-relaxed">{uiError}</p>
            <button
              type="button"
              onClick={() => setUiError(null)}
              className="shrink-0 rounded-lg p-1 transition-opacity hover:opacity-75"
              aria-label="Dismiss error"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        </div>
      )}

      {/* ================================================================== */}
      {/* LEFT SIDEBAR — Conversations                                        */}
      {/* ================================================================== */}
      <aside
        className="w-[300px] shrink-0 flex flex-col border-r"
        style={{ background: "var(--sidebar-bg)", borderColor: "var(--border)" }}
      >
        {/* User identity + sign-out */}
        <div
          className="flex items-center gap-3 px-4 py-3.5 border-b"
          style={{ borderColor: "var(--border)" }}
        >
          <Avatar userId={userId} size={32} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold truncate" style={{ color: "var(--text-primary)" }}>
              {userLabel}
            </p>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Signed in
            </p>
          </div>

          {/* Sign-out button — icon, hover turns red */}
          <button
            onClick={handleSignOut}
            title="Sign out"
            className="p-1.5 rounded-lg transition-all duration-150"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "#EF4444")}
            onMouseLeave={(e) => ((e.currentTarget as HTMLButtonElement).style.color = "var(--text-muted)")}
          >
            {/* Log-out arrow icon */}
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M10 3H13a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H10M7 11l3-3-3-3M10 8H2"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        {/* New Chat button */}
        <div className="px-3 pt-3 pb-2">
          <button
            onClick={handleNewChat}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium text-white transition-all duration-150 hover:opacity-90"
            style={{ background: "var(--primary)" }}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M6.5 1.5v10M1.5 6.5h10" stroke="white" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
            New Chat
          </button>
        </div>

        {/* Conversations */}
        <div className="flex-1 overflow-y-auto px-2 pt-1">
          {conversations.length === 0 ? (
            <p className="px-3 py-8 text-xs text-center" style={{ color: "var(--text-muted)" }}>
              No conversations yet
            </p>
          ) : (
            conversations.map((conv) => (
              <ConversationItem
                key={conv.id}
                conv={conv}
                active={conv.id === activeConvId}
                onClick={() => selectConversation(conv.id)}
                onDelete={() => handleDeleteConversation(conv.id)}
              />
            ))
          )}
        </div>
      </aside>

      {/* ================================================================== */}
      {/* MAIN CHAT AREA                                                      */}
      {/* ================================================================== */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {!activeConvId ? (
          /* Empty state */
          <div className="flex-1 flex flex-col items-center justify-center gap-4 px-8 text-center">
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center"
              style={{ background: "var(--primary-light)" }}
            >
              <svg width="30" height="30" viewBox="0 0 32 32" fill="none">
                <path d="M8 24V8l10 8-10 8z" fill="var(--primary-dark)" fillOpacity="0.9" />
                <path d="M18 20V12l6 4-6 4z" fill="var(--primary)" fillOpacity="0.6" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
                Ask anything
              </h2>
              <p className="mt-1.5 text-sm" style={{ color: "var(--text-muted)" }}>
                Start a new chat or select a conversation.
                <br />
                Upload documents via the panel on the right.
              </p>
            </div>
            <button
              onClick={handleNewChat}
              className="mt-2 px-6 py-2.5 rounded-full text-sm font-medium text-white transition-all duration-150 hover:opacity-90"
              style={{ background: "var(--primary)" }}
            >
              Start a conversation
            </button>
          </div>
        ) : (
          <>
            {/* Message list */}
            <div className="flex-1 overflow-y-auto">
              <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
                {messages.length === 0 && !sending && (
                  <div className="text-center py-12">
                    <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                      Type a message to get started
                    </p>
                  </div>
                )}

                {messages.map((msg) =>
                  msg.role === "user" ? (
                    <UserMessage key={msg.id} message={msg} />
                  ) : (
                    <AssistantMessage key={msg.id} message={msg} />
                  )
                )}

                {/* Typing indicator */}
                {sending && (
                  <div className="flex justify-start">
                    <div
                      className="w-7 h-7 rounded-full flex items-center justify-center mr-2.5 shrink-0"
                      style={{ background: "var(--primary)" }}
                    >
                      <svg width="13" height="13" viewBox="0 0 32 32" fill="none">
                        <path d="M8 24V8l10 8-10 8z" fill="white" fillOpacity="0.9" />
                        <path d="M18 20V12l6 4-6 4z" fill="white" fillOpacity="0.6" />
                      </svg>
                    </div>
                    <div
                      className="px-4 py-3 rounded-2xl rounded-bl-sm border flex items-center gap-1.5"
                      style={{ background: "var(--surface)", borderColor: "var(--border)" }}
                    >
                      {[0, 150, 300].map((delay) => (
                        <span
                          key={delay}
                          className="w-1.5 h-1.5 rounded-full animate-bounce"
                          style={{
                            background: "var(--primary)",
                            animationDelay: `${delay}ms`,
                          }}
                        />
                      ))}
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Chat input with upload button */}
            <ChatInput
              onSend={handleSend}
              loading={sending}
              onUpload={() => setShowUpload(true)}
              placeholder={
                artifacts.length > 0
                  ? `Searching ${artifacts.length} document${artifacts.length !== 1 ? "s" : ""}\u2026`
                  : "Ask anything about your documents\u2026"
              }
            />
          </>
        )}
      </main>

      {/* ================================================================== */}
      {/* RIGHT SIDEBAR — My Files                                            */}
      {/* ================================================================== */}
      <aside
        className="w-[300px] shrink-0 flex flex-col border-l"
        style={{ background: "var(--sidebar-bg)", borderColor: "var(--border)" }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3.5 border-b"
          style={{ borderColor: "var(--border)" }}
        >
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              My Files
            </p>
            {artifacts.length > 0 && (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                {artifacts.length} document{artifacts.length !== 1 ? "s" : ""}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            {showHfSpaceButton && (
              <a
                href={HF_SPACE_PAGE_URL}
                target="_blank"
                rel="noreferrer"
                title="Open the full Hugging Face Space page"
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 hover:opacity-80"
                style={{
                  background: "transparent",
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border)",
                }}
              >
                <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                  <path
                    d="M4 2h5v5M9 2L5.25 5.75M7 9H2V4"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                Space Page
              </a>
            )}

            {/* Upload button */}
            <button
              onClick={() => setShowUpload(true)}
              title="Upload documents"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 hover:opacity-80"
              style={{ background: "var(--primary)", color: "white" }}
            >
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M5.5 1v9M1 5.5h9" stroke="white" strokeWidth="1.7" strokeLinecap="round" />
              </svg>
              Upload
            </button>
          </div>
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto p-2">
          {uploadingFiles.length === 0 && artifacts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ background: "var(--primary-light)" }}
              >
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path
                    d="M9 14V6M5 10l4-4 4 4M2 16h14"
                    stroke="var(--primary-dark)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                No files yet.
                <br />
                Upload PDFs, DOCX or XLSX to get started.
              </p>
              <button
                onClick={() => setShowUpload(true)}
                className="text-xs font-medium transition-opacity hover:opacity-80"
                style={{ color: "var(--primary-dark)" }}
              >
                Upload your first document
              </button>
            </div>
          ) : (
            <>
              {/* Phantom entries for in-flight uploads */}
              {uploadingFiles.map((f) => (
                <ArtifactItem
                  key={`uploading-${f.tempId}`}
                  artifact={{
                    id: f.tempId,
                    filename: f.filename,
                    file_type: f.file_type,
                    size_bytes: f.size_bytes,
                    user_id: userId ?? "",
                    embedding_status: "none",
                    version_number: 1,
                    file_hash: "",
                    uploaded_by: userId ?? "",
                    upload_timestamp: "",
                    first_seen: "",
                    last_seen: "",
                    extracted_metadata: {},
                  }}
                  onDelete={() => {}}
                  uploadProgress={f.progress}
                />
              ))}
              {/* Real artifacts */}
              {artifacts.map((art) => (
                <ArtifactItem
                  key={art.id}
                  artifact={art}
                  onDelete={() => handleDeleteArtifact(art.id)}
                  onClick={() => setDetailArtifactId(art.id)}
                />
              ))}
            </>
          )}
        </div>
      </aside>

      {/* Upload modal — file picker only, closes immediately on selection */}
      {showUpload && (
        <FileUploadModal
          onFilesSelected={handleFilesSelected}
          onClose={() => setShowUpload(false)}
        />
      )}

      {/* Artifact detail modal */}
      {detailArtifactId && userId && (
        <ArtifactDetailModal
          artifactId={detailArtifactId}
          userId={userId}
          onClose={() => setDetailArtifactId(null)}
          onStatusChange={() => refreshArtifacts(userId)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export — wraps ChatInner in Suspense (required for useSearchParams)
// ---------------------------------------------------------------------------
export default function ChatPage() {
  return (
    <Suspense>
      <ChatInner />
    </Suspense>
  );
}
