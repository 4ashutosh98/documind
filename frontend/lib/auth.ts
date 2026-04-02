const KEY = "documind_user_id";

export function getUser(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(KEY);
}

export function setUser(userId: string): void {
  localStorage.setItem(KEY, userId);
}

export function clearUser(): void {
  localStorage.removeItem(KEY);
}

export function requireUser(): string {
  const u = getUser();
  if (!u) throw new Error("Not authenticated");
  return u;
}
