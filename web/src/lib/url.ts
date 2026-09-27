/** True when a URL points at this machine only (localhost, 127.0.0.0/8, ::1, 0.0.0.0), so another machine can't reach it. */
export function isLoopbackUrl(url: string): boolean {
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  host = host.replace(/^\[|\]$/g, "").replace(/\.$/, "");
  return host === "localhost" || host.endsWith(".localhost") || /^127\.\d+\.\d+\.\d+$/.test(host) || host === "::1" || host === "0.0.0.0";
}
