import { useEffect, useState, useRef } from "react";
import { Film, Trash2, RefreshCw, CheckCircle, Loader2, AlertCircle, ChevronDown, ChevronUp } from "lucide-react";
import { listVideos, deleteVideo } from "../api";

const STAGES = [
  { label: "Upload",       range: [0,  10] },
  { label: "Transcribe",   range: [10, 20] },
  { label: "Frames",       range: [20, 40] },
  { label: "AI Analysis",  range: [40, 82] },
  { label: "Instructions", range: [82, 90] },
  { label: "Indexing",     range: [90, 100] },
];

function fmtDuration(sec) {
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString();
}

function StageTrack({ progress, status }) {
  const pct = status === "completed" ? 100 : (progress || 0);
  return (
    <div className="stage-track">
      <div className="stage-track-bar">
        <div
          className={`stage-track-fill ${status === "failed" ? "failed" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="stage-track-steps">
        {STAGES.map(({ label, range }) => {
          const done = pct >= range[1] || status === "completed";
          const active = pct >= range[0] && pct < range[1] && status === "processing";
          return (
            <div key={label} className={`st-step ${done ? "done" : active ? "active" : ""}`}>
              <span className="st-dot">{done ? "✓" : active ? "●" : "○"}</span>
              <span className="st-label">{label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function VideoItem({ v, selectedId, onSelect, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const isProcessing = v.status === "processing" || v.status === "uploaded";

  return (
    <li className={`video-item ${selectedId === v.video_id ? "selected" : ""}`}>
      {/* Header row */}
      <div
        className="video-item-row"
        onClick={() => v.status === "completed" && onSelect(v)}
        style={{ cursor: v.status === "completed" ? "pointer" : "default" }}
      >
        <div className="video-item-main">
          {v.status === "completed" && <CheckCircle size={14} color="#22c55e" />}
          {v.status === "failed"    && <AlertCircle size={14} color="#ef4444" />}
          {isProcessing             && <Loader2 size={14} className="spin" color="#60a5fa" />}
          <span className="video-item-name" title={v.original_filename}>
            {v.original_filename}
          </span>
        </div>
        <div className="video-item-meta">
          {fmtDuration(v.duration) && <span>{fmtDuration(v.duration)}</span>}
          <span className={`status-badge status-${v.status}`}>
            {v.status}{isProcessing && v.progress > 0 ? ` ${v.progress}%` : ""}
          </span>
          <button className="icon-btn" onClick={e => { e.stopPropagation(); setExpanded(x => !x); }} title="Details">
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          <button className="icon-btn danger" onClick={e => onDelete(e, v.video_id)} title="Delete">
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="video-item-detail">
          <StageTrack progress={v.progress} status={v.status} />
          {v.message && (
            <div className={`detail-msg ${v.status === "failed" ? "detail-msg-err" : ""}`}>
              {v.message}
            </div>
          )}
          <div className="detail-meta">
            {v.fps > 0 && <span>{Math.round(v.fps)} fps</span>}
            {v.width > 0 && <span>{v.width}×{v.height}</span>}
            {v.total_frames > 0 && <span>{v.total_frames} frames</span>}
            {v.created_at && <span>{fmtDate(v.created_at)}</span>}
          </div>
        </div>
      )}
    </li>
  );
}

export default function VideoLibrary({ selectedId, onSelect, refreshTrigger }) {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef(null);

  const load = (silent = false) => {
    if (!silent) setLoading(true);
    listVideos()
      .then(res => { setVideos(res.data.videos || []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  // Poll every 3s while any video is still processing
  useEffect(() => {
    load();
    pollRef.current = setInterval(() => {
      setVideos(prev => {
        const hasProcessing = prev.some(v => v.status === "processing" || v.status === "uploaded");
        if (hasProcessing) load(true);
        return prev;
      });
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [refreshTrigger]);

  const handleDelete = async (e, id) => {
    e.stopPropagation();
    if (!window.confirm("Delete this video and all its data?")) return;
    await deleteVideo(id);
    load();
    if (selectedId === id) onSelect(null);
  };

  return (
    <div className="video-library">
      <div className="library-header">
        <Film size={15} /> <span>Video Library ({videos.length})</span>
        <button className="icon-btn" onClick={() => load()} title="Refresh">
          {loading ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
        </button>
      </div>

      {videos.length === 0 && !loading && (
        <div className="empty-library">No videos yet. Upload one above.</div>
      )}

      <ul className="video-list">
        {videos.map(v => (
          <VideoItem
            key={v.video_id}
            v={v}
            selectedId={selectedId}
            onSelect={onSelect}
            onDelete={handleDelete}
          />
        ))}
      </ul>
    </div>
  );
}
