// ---------------------------------------------------------------------------
// Ingestion / Artifacts
// ---------------------------------------------------------------------------

export interface ProvenanceRef {
  page?: number;
  section?: string;
  breadcrumb?: string;
  sheet?: string;
  char_start?: number;
  char_end?: number;
  row_start?: number;
  row_end?: number;
}

export interface ChunkResponse {
  id: string;
  artifact_id: string;
  chunk_index: number;
  text: string;
  chunk_type: string;
  provenance: ProvenanceRef;
  token_count?: number;
}

export interface ArtifactSummary {
  id: string;
  user_id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  file_hash: string;
  version_number: number;
  parent_id?: string;
  uploaded_by: string;
  upload_timestamp: string;
  first_seen: string;
  last_seen: string;
  extracted_metadata: Record<string, unknown>;
  embedding_status: "none" | "pending" | "ready";
}

export interface ArtifactDetail extends ArtifactSummary {
  chunks: ChunkResponse[];
}

export interface UploadResponse {
  artifact_id: string;
  status: "created" | "duplicate" | "new_version";
  version_number: number;
  message: string;
}

export interface DeleteResponse {
  artifact_id: string;
  blob_deleted: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

export interface QueryMatch {
  chunk: ChunkResponse;
  artifact: ArtifactSummary;
  score?: number;
  match_positions: [number, number][];
  search_type?: "keyword" | "semantic" | "hybrid";
}

export interface QueryResponse {
  query: string;
  total: number;
  results: QueryMatch[];
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export interface ConversationSummary {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface MessageResponse {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  query_results?: QueryResponse;
  created_at: string;
}

export interface SendMessageResponse {
  user_message: MessageResponse;
  assistant_message: MessageResponse;
}
