# VidyaBot — Voice-First YouTube Tutor

**What it is:** Paste any YouTube URL. Ask questions about the video — by voice or text, in Hindi or English. Get spoken answers with timestamp citations, powered entirely by Sarvam AI APIs.

**Who it's for:** Students, curious learners, and anyone who wants to go deeper on video content without scrubbing through timelines manually. Especially useful for Hindi-medium learners who prefer to interact in their native language.

**Why it matters:** Most AI tools assume English and text. VidyaBot is voice-first, Indic-language-aware, and grounded — it refuses to answer anything not in the video. That's a product decision, not a limitation.

---

## Architecture

```
YouTube URL
    │
    ▼
youtube-transcript-api  ──→  raw transcript with timestamps
    │
    ▼
Chunker (300 words, 50-word overlap, timestamp-preserving)
    │
    ▼
Gemini text-embedding-004  ──→  vector embeddings
    │
    ▼
ChromaDB (in-memory)  ──→  stored chunks + metadata

User speaks  ──→  MediaRecorder API (browser)
    │
    ▼
Sarvam Saaras v3 STT  ──→  transcribed text + detected language (hi/en)
    │
    ▼
Gemini embedding of question  ──→  top-3 relevant chunks (ChromaDB query)
    │
    ▼
Gemini 1.5 Flash  ──→  strict RAG answer (ONLY from transcript context)
    │
    ▼
Sarvam Bulbul v3 TTS  ──→  spoken answer (language-matched voice)
    │
    ▼
Frontend  ──→  chat bubble + timestamp citation chips → YouTube deeplinks
```

---

## Tech Stack

| Layer        | Technology                  | Why                                      |
|--------------|-----------------------------|------------------------------------------|
| STT          | Sarvam Saaras v3            | Best-in-class Hindi/English ASR          |
| TTS          | Sarvam Bulbul v3            | Natural Indian voices, 30+ speakers      |
| LLM          | Gemini 1.5 Flash            | Fast, free tier, great instruction-follow|
| Embeddings   | Gemini text-embedding-004   | Paired with LLM for coherent retrieval   |
| Vector DB    | ChromaDB (in-memory)        | Zero setup, perfect for single-video RAG |
| Backend      | FastAPI (Python)            | Clean async REST, easy Render deploy     |
| Frontend     | React + custom CSS          | Fast, no framework overhead              |
| Deployment   | Render.com (free tier)      | Live URL in < 5 minutes                  |

---

## Setup

### 1. Clone and configure

```bash
git clone <your-repo>
```

**Backend:**
```bash
cd backend
cp .env.example .env
# Fill in SARVAM_API_KEY and GEMINI_API_KEY in .env
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
cp .env.example .env.local
# Set REACT_APP_API_URL=http://localhost:8000 for local dev
npm install
npm start
```

### 2. Get API keys

- **Sarvam API key:** Sign up at [dashboard.sarvam.ai](https://dashboard.sarvam.ai)
- **Gemini API key:** Get it from [aistudio.google.com](https://aistudio.google.com)

### 3. Deploy to Render

1. Push backend folder to a GitHub repo
2. Go to [render.com](https://render.com) → New Web Service → connect repo
3. Set env vars: `SARVAM_API_KEY`, `GEMINI_API_KEY`
4. Deploy — Render gives you a live URL
5. Update `REACT_APP_API_URL` in frontend `.env.local` to the Render URL
6. Deploy frontend to Render Static Site or Vercel

---

## How to use

1. Paste any YouTube URL with captions (English or Hindi)
2. Wait ~10 seconds for transcript to load and embed
3. **Hold the mic button** and ask your question in Hindi or English
4. Release — VidyaBot transcribes, retrieves, answers, and speaks back
5. Click the timestamp chips to jump to that exact moment in the video

---

## Key design decisions

**Why no hallucination:** The system prompt explicitly instructs Gemini to answer only from the provided context chunks and say "This topic is not covered in the video" otherwise. This is enforced at the prompt level, not hoped for.

**Why Sarvam for STT/TTS:** It's the assignment requirement — but it's also genuinely the right tool. Saaras v3 handles Hindi-English code-mixing that Whisper struggles with. Bulbul v3 produces natural Indian-accented speech that feels right to the target user.

**Why ChromaDB in-memory:** For a single-video tutor, there's no need to persist vectors. In-memory means zero setup, zero cost, zero latency overhead. A production version would use Pinecone or Supabase pgvector.

**Why overlapping chunks:** A 50-word overlap between 300-word chunks ensures that answers near chunk boundaries aren't cut off mid-sentence. Small detail, big quality difference.

---

## Project structure

```
vidyabot/
├── backend/
│   ├── main.py           # FastAPI app — all endpoints with comments
│   ├── requirements.txt
│   ├── render.yaml       # Render deployment config
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.js        # Full React app — UI + voice + API calls
│   │   ├── App.css       # Dark UI styles
│   │   └── index.css     # Base reset + fonts
│   └── .env.example
└── README.md
```

---

## Sarvam APIs used

- **`/speech-to-text`** — Saaras v3, language auto-detection, handles Hindi + English
- **`/text-to-speech`** — Bulbul v3, language-matched speaker (Anushka for Hindi, Arvind for English)

Both are called from FastAPI endpoints that proxy the browser audio/text to Sarvam's REST API with the `api-subscription-key` header.
