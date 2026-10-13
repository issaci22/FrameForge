// Excluded folders, checked as you type. Mirrors server/frameforge_server/services/exclusions.py, which has the
// final say: absolute container paths strictly inside a library folder, compared ignoring case, whole folders only.

/** Forward slashes, no "." / ".." / empty segments, no trailing slash. */
export function normFolder(raw: string): string {
  const s = raw.trim().replace(/\\/g, "/");
  const absolute = s.startsWith("/");
  const parts: string[] = [];
  for (const seg of s.split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  return (absolute ? "/" : "") + parts.join("/");
}

const key = (p: string) => p.toLowerCase();
const within = (child: string, parent: string) => parent === "/" || key(child) === key(parent) || key(child).startsWith(`${key(parent)}/`);

/** The library folder an excluded folder sits in, if any. */
export function rootOf(path: string, roots: string[]): string | undefined {
  return roots
    .map(normFolder)
    .filter((r) => r.startsWith("/") && within(path, r))
    .sort((a, b) => b.length - a.length)[0];
}

/** Split for display: the library folder (shown faint) and the excluded part below it. */
export function splitExcluded(path: string, roots: string[]): { root: string | null; rest: string } {
  const root = rootOf(path, roots);
  if (!root || key(root) === key(path)) return { root: null, rest: path };
  return { root, rest: path.slice(root === "/" ? 1 : root.length + 1) };
}

/** Validate a new entry against the library folders and the current list. */
export function checkExclusion(raw: string, roots: string[], existing: string[]): { path: string } | { error: string } {
  const clean = roots.map(normFolder).filter((r) => r.startsWith("/"));
  let path = normFolder(raw);
  if (!path) return { error: "Enter a folder to exclude." };
  if (!path.startsWith("/")) {
    if (clean.length !== 1) return { error: "Use the full path: this library has several folders." };
    path = normFolder(`${clean[0]}/${path}`);
  }
  if (clean.some((r) => key(r) === key(path))) return { error: `${path} is a library folder itself. Remove it from Folders instead of excluding it.` };
  if (!clean.some((r) => within(path, r))) return { error: `${path} isn't inside this library's folders.` };
  const covering = existing.find((e) => within(path, e));
  if (covering) return { error: key(covering) === key(path) ? `${path} is already excluded.` : `Already excluded: it's inside ${covering}.` };
  return { path };
}
