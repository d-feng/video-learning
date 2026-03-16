import { Clock, Tag } from "lucide-react";

function fmt(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function ScoreBadge({ score }) {
  const color = score >= 80 ? "#22c55e" : score >= 50 ? "#f59e0b" : "#6b7280";
  return (
    <span style={{ background: color, color: "#fff", borderRadius: 4, padding: "2px 6px", fontSize: 11, fontWeight: 700 }}>
      {score}%
    </span>
  );
}

export default function SearchResults({ results, query, processingMs }) {
  if (!results) return null;

  return (
    <div className="search-results">
      <div className="results-header">
        <span>{results.length} result{results.length !== 1 ? "s" : ""} for <em>"{query}"</em></span>
        <span className="results-time">{processingMs?.toFixed(0)}ms</span>
      </div>

      {results.length === 0 && (
        <div className="no-results">No matching segments found. Try different keywords or switch to "text" mode.</div>
      )}

      <div className="results-grid">
        {results.map((r) => (
          <div key={r.segment_id} className="result-card">
            <div className="result-thumb-wrap">
              <img
                src={`http://localhost:8000${r.thumbnail_path}`}
                alt={`frame at ${fmt(r.timestamp)}`}
                className="result-thumb"
                onError={e => { e.target.style.display = "none"; }}
              />
              <span className="thumb-time"><Clock size={11} /> {fmt(r.timestamp)}</span>
            </div>

            <div className="result-body">
              <div className="result-top">
                <ScoreBadge score={r.similarity_score} />
                {r.video_name && <span className="video-name">{r.video_name}</span>}
              </div>

              {r.description && (
                <p className="result-description">{r.description}</p>
              )}

              {r.transcript && r.transcript !== "[no speech]" && (
                <blockquote className="result-transcript">"{r.transcript}"</blockquote>
              )}

              {(r.objects?.length > 0 || r.actions?.length > 0) && (
                <div className="result-tags">
                  {r.objects.map(o => (
                    <span key={o} className="tag tag-obj"><Tag size={9} /> {o}</span>
                  ))}
                  {r.actions.map(a => (
                    <span key={a} className="tag tag-act">{a}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
