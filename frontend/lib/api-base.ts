const EXPLICIT_API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").trim();

let hasLoggedMisconfiguredApiBase = false;

function normalizeApiBase(base: string): string {
  return base.replace(/\/+$/, "");
}

function isLocalHostname(hostname: string): boolean {
  return (
    hostname === "localhost"
    || hostname === "127.0.0.1"
    || hostname === "::1"
  );
}

function pointsToLocalApi(base: string): boolean {
  return /^(https?:\/\/)(localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?$/i.test(base);
}

export function getApiBase(): string {
  return EXPLICIT_API_BASE ? normalizeApiBase(EXPLICIT_API_BASE) : "";
}

export function getApiBaseDiagnostic(): string | null {
  if (typeof window === "undefined") return null;

  const base = getApiBase();
  if (!base) return null;
  if (isLocalHostname(window.location.hostname)) return null;
  if (!pointsToLocalApi(base)) return null;

  return [
    "This deployment is pointing at a local API server",
    `(${base}) instead of the Hugging Face Space backend.`,
    "The app can seem to work only on machines that also have a backend",
    "running on localhost:8000.",
  ].join(" ");
}

export function warnIfApiBaseLooksMisconfigured(): string | null {
  const diagnostic = getApiBaseDiagnostic();
  if (!diagnostic || hasLoggedMisconfiguredApiBase) return diagnostic;

  hasLoggedMisconfiguredApiBase = true;
  console.warn("[DocuMind] Misconfigured API base:", diagnostic);
  return diagnostic;
}
