import { useEffect, useState } from "react";
import { Database, CheckCircle, AlertCircle, Loader2 } from "lucide-react";
import { getVideoStats } from "../api";

export default function DBStats({ videoId }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!videoId) { setStats(null); return; }
    setLoading(true);
    setError(null);
    getVideoStats(videoId)
      .then(res => { setStats(res.data); setLoading(false); })
      .catch(() => { setError("Could not load stats"); setLoading(false); });
  }, [videoId]);

  if (!videoId) return null;

  return (
    <div className="db-stats">
      <div className="db-stats-header">
        <Database size={14} />
        <span>Database Summary</span>
        {loading && <Loader2 size={13} className="spin" />}
      </div>

      {error && <div className="db-stats-error"><AlertCircle size={12} /> {error}</div>}

      {stats && (
        <div className="db-stats-grid">
          <div className="db-stat">
            <span className="db-stat-value">{stats.segments_in_db}</span>
            <span className="db-stat-label">Segments stored</span>
          </div>
          <div className="db-stat">
            <span className="db-stat-value">{stats.vectors_in_chroma}</span>
            <span className="db-stat-label">Vectors (ChromaDB)</span>
          </div>
          <div className="db-stat">
            <span className="db-stat-value">{stats.instruction_steps}</span>
            <span className="db-stat-label">Instruction steps</span>
          </div>
          <div className="db-stat">
            <span className="db-stat-value">{stats.total_vectors_all_videos}</span>
            <span className="db-stat-label">Total vectors (all)</span>
          </div>

          <div className="db-stat-row">
            {stats.instructions_saved
              ? <><CheckCircle size={12} color="#4ade80" /> <span>Instructions saved</span></>
              : <><AlertCircle size={12} color="#f59e0b" /> <span>No instructions yet</span></>
            }
          </div>

          <div className="db-stat-row">
            {stats.vectors_in_chroma > 0
              ? <><CheckCircle size={12} color="#4ade80" /> <span>Vectors indexed — search is ready</span></>
              : <><AlertCircle size={12} color="#f59e0b" /> <span>No vectors — search won't work yet</span></>
            }
          </div>
        </div>
      )}
    </div>
  );
}
