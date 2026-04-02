import type { MessageResponse } from "@/types";

interface Props {
  message: MessageResponse;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function UserMessage({ message }: Props) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] flex flex-col items-end gap-1">
        <div
          className="px-4 py-3 rounded-2xl rounded-br-sm text-white text-sm leading-relaxed"
          style={{ background: "var(--user-bubble)" }}
        >
          {message.content}
        </div>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {formatTime(message.created_at)}
        </span>
      </div>
    </div>
  );
}
