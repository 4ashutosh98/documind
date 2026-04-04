import type {
  ArtifactDetail,
  ArtifactSummary,
  ConversationSummary,
  DeleteResponse,
  MessageResponse,
  QueryResponse,
  SendMessageResponse,
  UploadResponse,
} from "@/types";
import { getApiBase, warnIfApiBaseLooksMisconfigured } from "@/lib/api-base";

const BASE = getApiBase();

function getGroqKey(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("documind_groq_key") ?? "";
}

function getGoogleKey(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("documind_google_key") ?? "";
}

function buildHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  const groqKey = getGroqKey();
  const googleKey = getGoogleKey();

  if (groqKey) headers.set("X-Groq-Api-Key", groqKey);
  if (googleKey) headers.set("X-Google-Api-Key", googleKey);

  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return headers;
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  warnIfApiBaseLooksMisconfigured();
  const res = await fetch(`${BASE}${path}`, {
    headers: buildHeaders(init),
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export async function listArtifacts(userId: string): Promise<ArtifactSummary[]> {
  return request(`/artifacts?user_id=${encodeURIComponent(userId)}`);
}

export async function getArtifact(artifactId: string): Promise<ArtifactDetail> {
  return request(`/artifacts/${artifactId}`);
}

export async function uploadFile(
  file: File,
  userId: string,
  onProgress?: (pct: number) => void
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", userId);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    warnIfApiBaseLooksMisconfigured();
    xhr.open("POST", `${BASE}/upload`);
    const groqKey = getGroqKey();
    if (groqKey) xhr.setRequestHeader("X-Groq-Api-Key", groqKey);
    const googleKey = getGoogleKey();
    if (googleKey) xhr.setRequestHeader("X-Google-Api-Key", googleKey);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResponse);
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.statusText}`));
      }
    };
    xhr.onerror = () => {
      console.error("Upload request failed before the server returned a response.");
      reject(new Error("Network error during upload"));
    };
    xhr.send(form);
  });
}

export async function deleteArtifact(
  artifactId: string,
  userId: string
): Promise<DeleteResponse> {
  return request(
    `/artifacts/${artifactId}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" }
  );
}

/**
 * Open an SSE connection that pushes artifact list updates while any artifact
 * is pending indexing.  The server closes the stream once all are done and
 * fires an "done" event.
 *
 * Returns the EventSource so the caller can close it early if needed
 * (e.g. on component unmount).
 */
export function streamArtifactStatus(
  userId: string,
  onUpdate: (artifacts: ArtifactSummary[]) => void,
  onDone: () => void
): EventSource {
  const sse = new EventSource(
    `${BASE}/artifacts/stream?user_id=${encodeURIComponent(userId)}`
  );
  sse.onmessage = (e) => {
    try {
      onUpdate(JSON.parse(e.data) as ArtifactSummary[]);
    } catch {
      // ignore malformed events
    }
  };
  sse.addEventListener("done", () => {
    sse.close();
    onDone();
  });
  sse.onerror = () => {
    // Connection error or server closed — stop listening
    sse.close();
    onDone();
  };
  return sse;
}

export async function reembedArtifact(
  artifactId: string,
  userId: string
): Promise<{ artifact_id: string; embedding_status: string; message: string }> {
  return request(
    `/artifacts/${artifactId}/reembed?user_id=${encodeURIComponent(userId)}`,
    { method: "POST" }
  );
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

export async function queryDocuments(
  q: string,
  userId: string,
  artifactIds?: string[],
  limit = 10
): Promise<QueryResponse> {
  return request("/query", {
    method: "POST",
    body: JSON.stringify({ q, user_id: userId, artifact_ids: artifactIds ?? null, limit }),
  });
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export async function createConversation(
  userId: string
): Promise<ConversationSummary> {
  return request("/conversations", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function listConversations(
  userId: string
): Promise<ConversationSummary[]> {
  return request(`/conversations?user_id=${encodeURIComponent(userId)}`);
}

export async function deleteConversation(
  convId: string,
  userId: string
): Promise<void> {
  return request(
    `/conversations/${convId}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" }
  );
}

export async function sendMessage(
  convId: string,
  userId: string,
  content: string,
  artifactIds?: string[]
): Promise<SendMessageResponse> {
  return request(`/conversations/${convId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      content,
      artifact_ids: artifactIds ?? null,
    }),
  });
}

export async function getMessages(
  convId: string,
  userId: string
): Promise<MessageResponse[]> {
  return request(
    `/conversations/${convId}/messages?user_id=${encodeURIComponent(userId)}`
  );
}
