import React, { useState, useRef, useEffect, useCallback } from "react";
import "./App.css";

// ── API base URL — change this to your Render deployment URL ────────────────
const API = process.env.REACT_APP_API_URL || "http://localhost:8000";

// ── Small helper: format seconds to YouTube deep-link ───────────────────────
function ytLink(videoId, seconds) {
  return `https://www.youtube.com/watch?v=${videoId}&t=${seconds}s`;
}

// ── Mic button states ────────────────────────────────────────────────────────
const MIC_IDLE      = "idle";
const MIC_RECORDING = "recording";
const MIC_LOADING   = "loading";

export default function App() {
  // ── State ──────────────────────────────────────────────────────────────────
  const [url,        setUrl]        = useState("");
  const [videoInfo,  setVideoInfo]  = useState(null);   // {video_id, summary, chunk_count}
  const [loading,    setLoading]    = useState(false);   // loading video
  const [messages,   setMessages]   = useState([]);      // chat history
  const [micState,   setMicState]   = useState(MIC_IDLE);
  const [typedQ,     setTypedQ]     = useState("");      // text input fallback
  const [error,      setError]      = useState("");
  const [demos,      setDemos]      = useState([]);      // cached demo videos

  // ── Refs ───────────────────────────────────────────────────────────────────
  const mediaRecorder = useRef(null);
  const audioChunks   = useRef([]);
  const chatEndRef    = useRef(null);
  const audioRef      = useRef(null);   // for TTS playback

  // ── Auto-scroll chat ───────────────────────────────────────────────────────
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── Demo videos (transcripts cached on the server, always load) ──────────
  useEffect(() => {
    fetch(`${API}/demos`)
      .then(r => (r.ok ? r.json() : { demos: [] }))
      .then(d => setDemos(d.demos || []))
      .catch(() => setDemos([]));
  }, []);

  function handleLoadVideo(e) {
    e.preventDefault();
    loadVideo(url);
  }

  function loadDemo(videoId) {
    const demoUrl = `https://www.youtube.com/watch?v=${videoId}`;
    setUrl(demoUrl);
    loadVideo(demoUrl);
  }

  // ── Load video ─────────────────────────────────────────────────────────────
  async function loadVideo(targetUrl) {
    if (!targetUrl.trim()) return;
    setLoading(true);
    setError("");
    setMessages([]);
    setVideoInfo(null);

    try {
      const res  = await fetch(`${API}/load-video`, {
        method : "POST",
        headers: { "Content-Type": "application/json" },
        body   : JSON.stringify({ url: targetUrl.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to load video");

      setVideoInfo(data);
      // Welcome message from tutor
      setMessages([{
        role     : "tutor",
        text     : `I've read the full transcript of this video (${data.chunk_count} sections). ${data.summary}\n\nAsk me anything about it — in Hindi or English.`,
        citations: [],
      }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ── Send question (text or transcribed) ────────────────────────────────────
  const sendQuestion = useCallback(async (question, language = "en") => {
    if (!question.trim() || !videoInfo) return;

    // Add user message immediately
    setMessages(prev => [...prev, { role: "user", text: question, language }]);
    setMicState(MIC_LOADING);

    try {
      // 1. Get answer from RAG
      const askRes  = await fetch(`${API}/ask`, {
        method : "POST",
        headers: { "Content-Type": "application/json" },
        body   : JSON.stringify({ question, language }),
      });
      const askData = await askRes.json();
      if (!askRes.ok) throw new Error(askData.detail || "Answer failed");

      // 2. Add tutor message
      setMessages(prev => [...prev, {
        role     : "tutor",
        text     : askData.answer,
        citations: askData.citations || [],
      }]);

      // 3. Convert answer to speech (Sarvam TTS)
      const speakRes  = await fetch(`${API}/speak`, {
        method : "POST",
        headers: { "Content-Type": "application/json" },
        body   : JSON.stringify({ text: askData.answer, language }),
      });
      const speakData = await speakRes.json();
      if (speakRes.ok && speakData.audio_base64) {
        // Decode base64 WAV and play it
        const binary = atob(speakData.audio_base64);
        const bytes  = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        const blob   = new Blob([bytes], { type: "audio/wav" });
        const blobUrl = URL.createObjectURL(blob);
        if (audioRef.current) {
          audioRef.current.src = blobUrl;
          audioRef.current.play().catch(() => {});   // autoplay may need user gesture
        }
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        role: "tutor",
        text: `Sorry, something went wrong: ${err.message}`,
        citations: [],
      }]);
    } finally {
      setMicState(MIC_IDLE);
    }
  }, [videoInfo]);

  // ── Start recording ────────────────────────────────────────────────────────
  async function startRecording() {
    if (!videoInfo) return;
    setError("");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr     = new MediaRecorder(stream, { mimeType: "audio/webm" });
      audioChunks.current = [];

      mr.ondataavailable = e => audioChunks.current.push(e.data);
      mr.onstop = async () => {
        // Stop mic stream
        stream.getTracks().forEach(t => t.stop());

        const blob = new Blob(audioChunks.current, { type: "audio/webm" });
        const form = new FormData();
        form.append("audio", blob, "question.webm");

        setMicState(MIC_LOADING);

        try {
          // Sarvam STT
          const sttRes  = await fetch(`${API}/transcribe`, { method: "POST", body: form });
          const sttData = await sttRes.json();
          if (!sttRes.ok) throw new Error(sttData.detail || "Transcription failed");

          const { text, language } = sttData;
          if (text.trim()) {
            await sendQuestion(text, language);
          } else {
            setMicState(MIC_IDLE);
            setError("Could not hear anything. Please try again.");
          }
        } catch (err) {
          setMicState(MIC_IDLE);
          setError(err.message);
        }
      };

      mr.start();
      mediaRecorder.current = mr;
      setMicState(MIC_RECORDING);
    } catch (err) {
      setError("Microphone access denied. Please allow mic permissions.");
    }
  }

  // ── Stop recording ─────────────────────────────────────────────────────────
  function stopRecording() {
    if (mediaRecorder.current && micState === MIC_RECORDING) {
      mediaRecorder.current.stop();
    }
  }

  // ── Text input submit ──────────────────────────────────────────────────────
  async function handleTextSubmit(e) {
    e.preventDefault();
    if (!typedQ.trim()) return;
    const q = typedQ.trim();
    setTypedQ("");
    // Simple language detection: if text contains Devanagari, mark as Hindi
    const isHindi = /[\u0900-\u097F]/.test(q);
    await sendQuestion(q, isHindi ? "hi" : "en");
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="app">
      {/* Hidden audio player for TTS playback */}
      <audio ref={audioRef} style={{ display: "none" }} />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon">▶</span>
            <span className="logo-text">VidyaBot</span>
            <span className="logo-tag">by Sarvam AI</span>
          </div>
          <div className="header-pills">
            <span className="pill pill-purple">Saaras v3 STT</span>
            <span className="pill pill-purple">Bulbul v3 TTS</span>
            <span className="pill pill-blue">Gemini RAG</span>
          </div>
        </div>
      </header>

      <main className="main">
        {/* ── URL Input ──────────────────────────────────────────────────── */}
        <section className="url-section">
          <form onSubmit={handleLoadVideo} className="url-form">
            <div className="url-input-wrap">
              <span className="url-icon">🔗</span>
              <input
                className="url-input"
                type="text"
                placeholder="Paste a YouTube URL to start learning..."
                value={url}
                onChange={e => setUrl(e.target.value)}
                disabled={loading}
              />
            </div>
            <button
              type="submit"
              className={`load-btn ${loading ? "loading" : ""}`}
              disabled={loading || !url.trim()}
            >
              {loading ? <span className="spinner" /> : "Load Video"}
            </button>
          </form>
          {demos.length > 0 && (
            <div className="demo-row">
              <span className="demo-label">Try a demo:</span>
              {demos.map(d => (
                <button
                  key={d.video_id}
                  type="button"
                  className="demo-chip"
                  onClick={() => loadDemo(d.video_id)}
                  disabled={loading}
                  title={d.title}
                >
                  ▶ {d.title.length > 48 ? d.title.slice(0, 48) + "…" : d.title}
                </button>
              ))}
            </div>
          )}
          {error && <div className="error-bar">{error}</div>}
        </section>

        {/* ── Video Info Card ─────────────────────────────────────────────── */}
        {videoInfo && (
          <div className="video-card">
            <div className="video-card-left">
              <img
                src={`https://img.youtube.com/vi/${videoInfo.video_id}/mqdefault.jpg`}
                alt="Video thumbnail"
                className="thumb"
              />
            </div>
            <div className="video-card-right">
              <div className="video-meta">
                <span className="badge badge-green">✓ Loaded</span>
                <span className="badge badge-gray">{videoInfo.chunk_count} sections</span>
                <span className="badge badge-gray">{videoInfo.duration}</span>
              </div>
              <p className="video-summary">{videoInfo.summary}</p>
            </div>
          </div>
        )}

        {/* ── Chat ──────────────────────────────────────────────────────────── */}
        {messages.length > 0 && (
          <section className="chat-section">
            <div className="chat-log">
              {messages.map((msg, i) => (
                <div key={i} className={`message message-${msg.role}`}>
                  <div className="message-avatar">
                    {msg.role === "tutor" ? "🤖" : "🎤"}
                  </div>
                  <div className="message-body">
                    <div className="message-text">{msg.text}</div>
                    {/* Timestamp citations */}
                    {msg.citations && msg.citations.length > 0 && (
                      <div className="citations">
                        <span className="citations-label">Source:</span>
                        {msg.citations.map((c, ci) => (
                          <a
                            key={ci}
                            href={ytLink(videoInfo?.video_id, c.seconds)}
                            target="_blank"
                            rel="noreferrer"
                            className="citation-chip"
                          >
                            ▶ {c.timestamp}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {/* Loading indicator */}
              {micState === MIC_LOADING && (
                <div className="message message-tutor">
                  <div className="message-avatar">🤖</div>
                  <div className="message-body">
                    <div className="typing-dots">
                      <span /><span /><span />
                    </div>
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          </section>
        )}

        {/* ── Input area (only shown after video loads) ─────────────────── */}
        {videoInfo && (
          <section className="input-section">
            {/* Language hint */}
            <div className="lang-hint">
              Ask in <strong>Hindi</strong> or <strong>English</strong> — VidyaBot will respond in the same language
            </div>

            <div className="input-row">
              {/* Hold-to-speak mic button */}
              <button
                className={`mic-btn mic-${micState}`}
                onMouseDown={startRecording}
                onMouseUp={stopRecording}
                onTouchStart={e => { e.preventDefault(); startRecording(); }}
                onTouchEnd={e => { e.preventDefault(); stopRecording(); }}
                disabled={micState === MIC_LOADING}
                aria-label="Hold to speak"
                title="Hold to speak"
              >
                {micState === MIC_RECORDING ? (
                  <span className="mic-recording-ring">
                    <MicIcon />
                  </span>
                ) : micState === MIC_LOADING ? (
                  <span className="spinner" />
                ) : (
                  <MicIcon />
                )}
              </button>

              {/* Text input as fallback */}
              <form onSubmit={handleTextSubmit} className="text-form">
                <input
                  type="text"
                  className="text-input"
                  placeholder="Or type your question here..."
                  value={typedQ}
                  onChange={e => setTypedQ(e.target.value)}
                  disabled={micState !== MIC_IDLE}
                />
                <button
                  type="submit"
                  className="send-btn"
                  disabled={!typedQ.trim() || micState !== MIC_IDLE}
                >
                  Send
                </button>
              </form>
            </div>

            {micState === MIC_RECORDING && (
              <div className="recording-hint">🔴 Recording... release to send</div>
            )}
          </section>
        )}

        {/* ── Empty state ────────────────────────────────────────────────── */}
        {!videoInfo && !loading && (
          <div className="empty-state">
            <div className="empty-icon">🎓</div>
            <h2 className="empty-title">Learn anything from any YouTube video</h2>
            <p className="empty-sub">Paste a URL above. Ask questions by voice or text. Get answers with timestamps — in Hindi or English.</p>
            <div className="feature-grid">
              <div className="feature-card">
                <span>🎤</span>
                <strong>Voice first</strong>
                <p>Hold mic button, ask in Hindi or English</p>
              </div>
              <div className="feature-card">
                <span>🔒</span>
                <strong>No hallucination</strong>
                <p>Answers only from the video transcript</p>
              </div>
              <div className="feature-card">
                <span>⏱️</span>
                <strong>Timestamp links</strong>
                <p>Every answer links back to the video moment</p>
              </div>
              <div className="feature-card">
                <span>🇮🇳</span>
                <strong>Bilingual</strong>
                <p>Powered by Sarvam AI — built for Bharat</p>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

// ── Mic SVG icon ─────────────────────────────────────────────────────────────
function MicIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
      <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
      <line x1="12" y1="19" x2="12" y2="23"/>
      <line x1="8"  y1="23" x2="16" y2="23"/>
    </svg>
  );
}
