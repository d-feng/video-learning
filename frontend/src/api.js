import axios from "axios";

const api = axios.create({ baseURL: "http://localhost:8000" });

export const uploadVideo = (formData, onProgress) =>
  api.post("/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) =>
      onProgress && onProgress(Math.round((e.loaded / e.total) * 100)),
  });

export const uploadUrl = (url) => {
  const form = new FormData();
  form.append("url", url);
  return api.post("/upload", form);
};

export const uploadLocalPath = (path) => {
  const form = new FormData();
  form.append("local_path", path);
  return api.post("/upload", form);
};

export const searchVideo = (query, videoId, topK = 8, searchType = "hybrid") =>
  api.post("/search", { query, video_id: videoId || null, top_k: topK, search_type: searchType });

export const listVideos = () => api.get("/videos");

export const getInstructions = (videoId) =>
  api.get(`/videos/${videoId}/instructions`);

export const getSegments = (videoId, limit = 50, offset = 0) =>
  api.get(`/videos/${videoId}/segments`, { params: { limit, offset } });

export const deleteVideo = (videoId) => api.delete(`/videos/${videoId}`);

export const reindexVideo = (videoId) => api.post(`/videos/${videoId}/reindex`);

export const getVideoStats = (videoId) => api.get(`/videos/${videoId}/stats`);

export const connectWS = (videoId, onMessage) => {
  const ws = new WebSocket(`ws://localhost:8000/ws/${videoId}`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  return ws;
};

export default api;
