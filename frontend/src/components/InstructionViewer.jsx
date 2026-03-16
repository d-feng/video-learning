import { useState, useEffect } from "react";
import { BookOpen, ChevronDown, ChevronUp, Clock, Package, Loader2 } from "lucide-react";
import { getInstructions } from "../api";

function fmt(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default function InstructionViewer({ videoId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState({});
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!videoId) return;
    setLoading(true);
    setError("");
    getInstructions(videoId)
      .then(res => { setData(res.data); setLoading(false); })
      .catch(e => {
        const msg = e.response?.status === 404
          ? "Instructions not ready yet — processing may still be running."
          : (e.response?.data?.detail || e.message);
        setError(msg);
        setLoading(false);
      });
  }, [videoId]);

  const toggle = (n) => setExpanded(prev => ({ ...prev, [n]: !prev[n] }));

  if (!videoId) return null;

  return (
    <div className="instruction-viewer">
      <button className="section-toggle" onClick={() => setOpen(v => !v)}>
        <BookOpen size={16} /> Step-by-Step Instructions
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {open && (
        <div className="instruction-body">
          {loading && <div className="center-msg"><Loader2 className="spin" size={20} /> Loading instructions…</div>}
          {error && <div className="error-msg">{error}</div>}

          {data && (
            <>
              <div className="instruction-header">
                <h3>{data.title}</h3>
                <p className="summary">{data.summary}</p>
                <span className="step-count">{data.total_steps} steps</span>
              </div>

              <div className="steps-list">
                {data.steps.map((step) => (
                  <div key={step.step_number} className="step-card">
                    <button className="step-header" onClick={() => toggle(step.step_number)}>
                      <span className="step-num">{step.step_number}</span>
                      <span className="step-title">{step.title}</span>
                      <span className="step-time"><Clock size={11} /> {fmt(step.timestamp)}</span>
                      {expanded[step.step_number] ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>

                    {expanded[step.step_number] && (
                      <div className="step-detail">
                        {step.thumbnail_path && (
                          <img
                            src={`http://localhost:8000${step.thumbnail_path}`}
                            alt={step.title}
                            className="step-thumb"
                            onError={e => { e.target.style.display = "none"; }}
                          />
                        )}
                        <div className="step-text">
                          <p>{step.description}</p>
                          {step.objects_needed?.length > 0 && (
                            <div className="objects-needed">
                              <Package size={13} /> <strong>You'll need:</strong>{" "}
                              {step.objects_needed.join(", ")}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
