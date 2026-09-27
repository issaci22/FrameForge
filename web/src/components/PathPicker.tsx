import { useEffect, useState } from "react";
import { ChevronLeft, Folder } from "lucide-react";
import { api } from "../api/client";
import { Modal } from "./ui";

interface Listing {
  path: string;
  parent: string | null;
  dirs: { name: string; path: string }[];
}

/** Browse folders as the container sees them. */
export function PathPicker({ initial, onPick, onClose }: { initial?: string; onPick: (path: string) => void; onClose: () => void }) {
  const [path, setPath] = useState(initial || "/media");
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .get<Listing>(`/system/browse?path=${encodeURIComponent(path)}`)
      .then((l) => !cancelled && setListing(l))
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        if (path !== "/") setPath("/");
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return (
    <Modal
      title="Choose a folder"
      subtitle="These are paths inside the FrameForge container. Mount host folders in docker-compose.yml to see them here."
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!listing} onClick={() => listing && onPick(listing.path)}>
            Use {listing?.path ?? path}
          </button>
        </>
      }
    >
      <div className="row mb-3">
        <button className="btn icon" disabled={!listing?.parent} onClick={() => listing?.parent && setPath(listing.parent)} aria-label="Up">
          <ChevronLeft size={16} />
        </button>
        <input className="input mono" value={path} onChange={(e) => setPath(e.target.value)} />
      </div>
      {error && <div className="error-text mb-3">{error}</div>}
      <div className="path-list">
        {listing?.dirs.length === 0 && <div className="none">No sub-folders</div>}
        {listing?.dirs.map((d) => (
          <button key={d.path} onClick={() => setPath(d.path)}>
            <Folder size={16} color="var(--accent)" />
            {d.name}
          </button>
        ))}
      </div>
    </Modal>
  );
}
