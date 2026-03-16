import { useState } from "react";
import { Search, Loader2, CheckCircle, AlertTriangle, RefreshCw } from "lucide-react";

export default function SearchBar({ onSearch, loading, disabled, vectorCount, onReindex, reindexing }) {
  const [query, setQuery] = useState("");
  const [searchType, setSearchType] = useState("hybrid");
  const [topK, setTopK] = useState(8);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    onSearch(query.trim(), searchType, topK);
  };

  return (
    <form className="search-bar" onSubmit={handleSubmit}>
      <div className="search-input-wrap">
        <Search size={18} className="search-icon" />
        <input
          className="search-input"
          placeholder="Search video content… e.g. 'how to attach the red wire'"
          value={query}
          onChange={e => setQuery(e.target.value)}
          disabled={disabled}
        />
        <button type="submit" className="btn-primary search-btn" disabled={disabled || loading || !query.trim()}>
          {loading ? <Loader2 size={16} className="spin" /> : "Search"}
        </button>
      </div>

      {vectorCount !== null && (
        <div className="search-status">
          {vectorCount > 0 ? (
            <span className="search-status-ready">
              <CheckCircle size={13} /> Semantic search ready ({vectorCount} vectors)
            </span>
          ) : (
            <span className="search-status-warn">
              <AlertTriangle size={13} /> Not indexed — text search only
              {onReindex && (
                <button className="btn-reindex" onClick={onReindex} disabled={reindexing}>
                  {reindexing ? <Loader2 size={12} className="spin" /> : <RefreshCw size={12} />}
                  {reindexing ? " Indexing..." : " Index now"}
                </button>
              )}
            </span>
          )}
        </div>
      )}

      <div className="search-options">
        <label>Mode:</label>
        {["hybrid", "semantic", "text"].map(t => (
          <label key={t} className="radio-label">
            <input type="radio" name="searchType" value={t}
              checked={searchType === t}
              onChange={() => setSearchType(t)}
            /> {t}
          </label>
        ))}
        <label style={{ marginLeft: 16 }}>Results:</label>
        <select value={topK} onChange={e => setTopK(Number(e.target.value))} className="select-sm">
          {[4, 8, 12, 20].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
    </form>
  );
}
