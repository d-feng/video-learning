import { useState, useEffect } from "react";
import VideoUploader from "./components/VideoUploader";
import VideoLibrary from "./components/VideoLibrary";
import SearchBar from "./components/SearchBar";
import SearchResults from "./components/SearchResults";
import InstructionViewer from "./components/InstructionViewer";
import DBStats from "./components/DBStats";
import { searchVideo, getVideoStats, reindexVideo } from "./api";
import "./App.css";

export default function App() {
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [libRefresh, setLibRefresh] = useState(0);
  const [searchResults, setSearchResults] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchMs, setSearchMs] = useState(null);
  const [vectorCount, setVectorCount] = useState(null);
  const [reindexing, setReindexing] = useState(false);

  // Refresh vector count whenever selected video changes or library refreshes
  useEffect(() => {
    if (!selectedVideo?.video_id) { setVectorCount(null); return; }
    getVideoStats(selectedVideo.video_id)
      .then(r => setVectorCount(r.data.vectors_in_chroma ?? 0))
      .catch(() => setVectorCount(0));
  }, [selectedVideo?.video_id, libRefresh]);

  const handleReindex = async () => {
    if (!selectedVideo?.video_id) return;
    setReindexing(true);
    try {
      await reindexVideo(selectedVideo.video_id);
      const r = await getVideoStats(selectedVideo.video_id);
      setVectorCount(r.data.vectors_in_chroma);
    } catch (e) {
      console.error(e);
    } finally {
      setReindexing(false);
    }
  };

  const handleVideoReady = (video) => {
    setLibRefresh(n => n + 1);
    setSelectedVideo(video);
  };

  const handleSearch = async (query, searchType, topK) => {
    setSearchLoading(true);
    setSearchQuery(query);
    try {
      const res = await searchVideo(query, selectedVideo?.video_id, topK, searchType);
      setSearchResults(res.data.results);
      setSearchMs(res.data.processing_time_ms);
    } catch (e) {
      console.error(e);
    } finally {
      setSearchLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-inner">
          <h1 className="app-title">Video Learning</h1>
          <p className="app-subtitle">
            Upload any instructional video → search by natural language → extract step-by-step guides
          </p>
        </div>
      </header>

      <main className="app-main">
        <aside className="sidebar">
          <VideoUploader onVideoReady={handleVideoReady} />
          <VideoLibrary
            selectedId={selectedVideo?.video_id}
            onSelect={setSelectedVideo}
            refreshTrigger={libRefresh}
          />
        </aside>

        <section className="content">
          {selectedVideo && (
            <div className="selected-banner">
              <strong>Active:</strong> {selectedVideo.original_filename}
              {selectedVideo.duration > 0 && (
                <span> — {Math.round(selectedVideo.duration)}s</span>
              )}
              <button className="clear-btn" onClick={() => setSelectedVideo(null)}>✕ Clear filter</button>
            </div>
          )}

          <SearchBar
            onSearch={handleSearch}
            loading={searchLoading}
            disabled={false}
            vectorCount={vectorCount}
            onReindex={selectedVideo?.video_id ? handleReindex : null}
            reindexing={reindexing}
          />

          {searchResults !== null && (
            <SearchResults
              results={searchResults}
              query={searchQuery}
              processingMs={searchMs}
            />
          )}

          {!searchResults && (
            <div className="welcome-hint">
              <p>Upload a video and wait for processing, then search its content above.</p>
              <p>Try: <em>"how to connect the motor"</em> · <em>"safety precautions"</em> · <em>"final assembly"</em></p>
            </div>
          )}

          {selectedVideo?.video_id && (
            <DBStats videoId={selectedVideo.video_id} />
          )}

          {selectedVideo?.video_id && (
            <InstructionViewer videoId={selectedVideo.video_id} />
          )}
        </section>
      </main>
    </div>
  );
}
