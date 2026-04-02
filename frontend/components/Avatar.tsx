const PALETTE = [
  "#7BB3F0",
  "#90CAF9",
  "#80DEEA",
  "#A5D6A7",
  "#FFCC80",
  "#F48FB1",
];

interface Props {
  userId: string;
  size?: number;
  className?: string;
}

export default function Avatar({ userId, size = 36, className = "" }: Props) {
  const index = parseInt(userId.replace(/\D/g, "") || "0", 10) % PALETTE.length;
  const bg = PALETTE[index];
  const initial = userId.replace(/[^a-zA-Z]/g, "")[0]?.toUpperCase() ?? "U";
  const fontSize = Math.round(size * 0.38);

  return (
    <div
      className={`flex items-center justify-center rounded-full shrink-0 font-semibold text-white select-none ${className}`}
      style={{ width: size, height: size, background: bg, fontSize }}
    >
      {initial}
    </div>
  );
}
