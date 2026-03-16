import { useState, useCallback, useRef } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, Link, FolderOpen, Loader2 } from "lucide-react";
import { uploadVideo, uploadUrl, uploadLocalPath, listVideos } from "../api";

const ACCEPTED = { "video/*": [".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"] };

export default function VideoUploader({ onVideoReady }) {
  const [mode, setMode] = useState("file");   // file | url | path
  const [urlInput, setUrlInput] = useState("");
  const [pathInput, setPathInput] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [processingPct, setProcessingPct] = useState(0);
  const [statusMsg, setStatusMsg] = useState("");
  const [error, setError] = useState("");

  const startProcessing = useCallback((videoId, videoMeta) => {
    setProcessingPct(0);
    setStatusMsg("Starting...");
    const interval = setInterval(async () => {
      try {
        const r = await listVideos();
        const video = r.data.videos.find(v => v.video_id === videoId);
        if (!video) return;
        setProcessingPct(video.progress || 0);
        setStatusMsg(video.message || "");
        if (video.status === "completed") {
          clearInterval(interval);
          setUploading(false);
          setStatusMsg("Done!");
          onVideoReady(video);
        } else if (video.status === "failed") {
          clearInterval(interval);
          setUploading(false);
          setError(video.message || "Processing failed");
        }
      } catch (e) { /* ignore poll errors */ }
    }, 2000);
  }, [onVideoReady]);

  const handleResponse = useCallback((res) => {
    const { video_id, video } = res.data;
    startProcessing(video_id, video);
  }, [startProcessing]);

  // File drop
  const onDrop = useCallback(async (files) => {
    if (!files.length) return;
    setError("");
    setUploading(true);
    setUploadPct(0);
    const form = new FormData();
    form.append("file", files[0]);
    try {
      const res = await uploadVideo(form, setUploadPct);
      handleResponse(res);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
      setUploading(false);
    }
  }, [handleResponse]);

  const fileInputRef = useRef(null);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED,
    disabled: uploading,
    multiple: false,
  });

  const handleUrl = async () => {
    if (!urlInput.trim()) return;
    setError("");
    setUploading(true);
    setUploadPct(0);
    try {
      const res = await uploadUrl(urlInput.trim());
      handleResponse(res);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
      setUploading(false);
    }
  };

  const handlePath = async () => {
    if (!pathInput.trim()) return;
    setError("");
    setUploading(true);
    try {
      const res = await uploadLocalPath(pathInput.trim());
      handleResponse(res);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
      setUploading(false);
    }
  };

  return (
    <div className="uploader">
      <h2>Add Tutorial Video</h2>

      {/* Mode tabs */}
      <div className="mode-tabs">
        {[["file", "Upload File", <Upload size={14} />],
          ["url",  "YouTube / URL", <Link size={14} />],
          ["path", "Local Path", <FolderOpen size={14} />]
        ].map(([id, label, icon]) => (
          <button
            key={id}
            className={`tab ${mode === id ? "active" : ""}`}
            onClick={() => setMode(id)}
            disabled={uploading}
          >
            {icon} {label}
          </button>
        ))}
      </div>

      {/* File drop zone */}
      {mode === "file" && (
        <>
          <div {...getRootProps()} className={`dropzone ${isDragActive ? "drag-over" : ""} ${uploading ? "disabled" : ""}`}>
            <input {...getInputProps()} />
            <Upload size={36} className="drop-icon" />
            {isDragActive
              ? <p>Drop it here…</p>
              : <p>Drag & drop a video here</p>
            }
            <p className="hint">MP4, MOV, AVI, MKV, WebM</p>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            style={{ display: "none" }}
            onChange={e => {
              const files = Array.from(e.target.files);
              if (files.length) onDrop(files);
              e.target.value = "";
            }}
          />
          <button
            className="btn-browse"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            <Upload size={15} /> Browse Computer
          </button>
        </>
      )}

      {/* URL input */}
      {mode === "url" && (
        <div className="input-row">
          <input
            className="text-input"
            placeholder="https://www.youtube.com/watch?v=..."
            value={urlInput}
            onChange={e => setUrlInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleUrl()}
            disabled={uploading}
          />
          <button className="btn-primary" onClick={handleUrl} disabled={uploading || !urlInput.trim()}>
            {uploading ? <Loader2 size={16} className="spin" /> : "Download & Process"}
          </button>
        </div>
      )}

      {/* Local path */}
      {mode === "path" && (
        <div className="input-row">
          <input
            className="text-input"
            placeholder="C:\videos\tutorial.mp4"
            value={pathInput}
            onChange={e => setPathInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handlePath()}
            disabled={uploading}
          />
          <button className="btn-primary" onClick={handlePath} disabled={uploading || !pathInput.trim()}>
            {uploading ? <Loader2 size={16} className="spin" /> : "Process"}
          </button>
        </div>
      )}

      {/* Progress */}
      {uploading && (
        <div className="progress-panel">
          {/* Overall bar */}
          <div className="progress-bar-wrap">
            <div
              className="progress-bar-fill"
              style={{ width: `${uploadPct < 100 ? uploadPct : processingPct}%` }}
            />
          </div>
          <div className="progress-pct">
            {uploadPct < 100 ? `Uploading ${uploadPct}%` : `${processingPct}%`}
          </div>

          {/* Stage indicators */}
          <div className="stages">
            {[
              { label: "Upload",      range: [0,  10] },
              { label: "Transcribe",  range: [10, 20] },
              { label: "Frames",      range: [20, 40] },
              { label: "AI Analysis", range: [40, 82] },
              { label: "Instructions",range: [82, 90] },
              { label: "Indexing",    range: [90, 100] },
            ].map(({ label, range }) => {
              const pct = uploadPct < 100 ? 0 : processingPct;
              const done = pct >= range[1];
              const active = pct >= range[0] && pct < range[1];
              return (
                <div key={label} className={`stage ${done ? "done" : active ? "active" : ""}`}>
                  <span className="stage-dot">{done ? "✓" : active ? "●" : "○"}</span>
                  <span className="stage-label">{label}</span>
                </div>
              );
            })}
          </div>

          {/* Current message */}
          {statusMsg && (
            <div className="progress-msg">
              <Loader2 size={13} className="spin" /> {statusMsg}
            </div>
          )}
        </div>
      )}

      {error && <div className="error-msg">{error}</div>}
    </div>
  );
}
