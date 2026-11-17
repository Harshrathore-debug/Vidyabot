"""
VidyaBot Backend — FastAPI
Endpoints:
  POST /load-video    → extract transcript, chunk, embed, store in ChromaDB
  POST /ask           → retrieve relevant chunks, answer via Gemini
  POST /transcribe    → Sarvam Saaras v3 STT (audio → text)
  POST /speak         → Sarvam Bulbul v3 TTS (text → audio)
  GET  /health        → health check
"""

import os
import re
import tempfile
import requests
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import chromadb
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

# ── Load environment variables ──────────────────────────────────────────────
load_dotenv()
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL     = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
EMBED_MODEL    = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# ── Configure Gemini (new google-genai SDK) ──────────────────────────────────
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ── ChromaDB — in-memory, no disk setup needed ───────────────────────────────
chroma_client   = chromadb.Client()
COLLECTION_NAME = "transcript"

# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title="VidyaBot API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic request models ──────────────────────────────────────────────────
class LoadVideoRequest(BaseModel):
    url: str

class AskRequest(BaseModel):
    question: str
    language: Optional[str] = "en"

class SpeakRequest(BaseModel):
    text: str
    language: Optional[str] = "en"


# ── Helper: extract YouTube video ID from any URL format ────────────────────
def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    raise ValueError(f"Cannot extract video ID from: {url}")


# ── Helper: merge transcript segments into overlapping word-chunks ───────────
def chunk_transcript(segments: list, chunk_size: int = 300, overlap: int = 50) -> list:
    """
    Each segment from YouTubeTranscriptApi looks like:
      {"text": "hello world", "start": 12.4, "duration": 2.1}

    We flatten all segments into a (word, start_time) list,
    then slide a window of chunk_size words with overlap words of overlap.
    Each chunk keeps the timestamp of its first word.
    """
    words = []
    for seg in segments:
        seg_words = seg["text"].split()
        for w in seg_words:
            words.append((w, seg["start"]))

    chunks    = []
    chunk_idx = 0
    i         = 0

    while i < len(words):
        window = words[i: i + chunk_size]
        text   = " ".join(w for w, _ in window)
        start  = window[0][1]
        chunks.append({
            "chunk_id": f"chunk_{chunk_idx}",
            "text"    : text,
            "start"   : round(start),
        })
        chunk_idx += 1
        i += chunk_size - overlap

    return chunks


# ── Helper: seconds → MM:SS string ──────────────────────────────────────────
def fmt_time(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


# ── POST /load-video ─────────────────────────────────────────────────────────
@app.post("/load-video")
async def load_video(req: LoadVideoRequest):
    """
    1. Extract video ID from URL
    2. Fetch transcript with timestamps via youtube-transcript-api
    3. Chunk into 300-word overlapping windows
    4. Embed each chunk with Gemini text-embedding-004
    5. Store embeddings + text + timestamps in ChromaDB
    6. Return summary, chunk count, duration
    """
    # Step 1: parse video ID
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Step 2: fetch transcript (English preferred, Hindi fallback)
    try:
        transcript = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-IN", "hi"]
        )
    except NoTranscriptFound:
        raise HTTPException(
            status_code=404,
            detail="No transcript found. Try a YouTube video that has captions enabled."
        )
    except TranscriptsDisabled:
        raise HTTPException(
            status_code=403,
            detail="Captions are disabled for this video."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcript fetch error: {str(e)}")

    # Step 3: chunk
    chunks = chunk_transcript(transcript, chunk_size=300, overlap=50)
    if not chunks:
        raise HTTPException(status_code=500, detail="Transcript came back empty.")

    # Step 4: embed all chunk texts in one API call
    texts = [c["text"] for c in chunks]
    try:
        emb_response = gemini_client.models.embed_content(
            model   = EMBED_MODEL,
            contents= texts,
            config  = types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        # emb_response.embeddings is a list of ContentEmbedding objects
        embeddings = [e.values for e in emb_response.embeddings]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding error: {str(e)}")

    # Step 5: store in ChromaDB — reset collection for fresh video load
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # collection didn't exist yet — fine

    collection = chroma_client.create_collection(COLLECTION_NAME)
    collection.add(
        ids        = [c["chunk_id"] for c in chunks],
        embeddings = embeddings,
        documents  = texts,
        metadatas  = [{"start": c["start"]} for c in chunks],
    )

    # Step 6: generate a 2-sentence summary for the UI welcome message
    preview = " ".join(texts)[:2000]
    try:
        summary_resp = gemini_client.models.generate_content(
            model   = CHAT_MODEL,
            contents= f"In 2 sentences, summarize what this video is about:\n\n{preview}",
        )
        summary = summary_resp.text.strip()
    except Exception:
        summary = "Video loaded successfully. Ask me anything about it."

    return {
        "video_id"   : video_id,
        "chunk_count": len(chunks),
        "duration"   : fmt_time(chunks[-1]["start"]) if chunks else "00:00",
        "summary"    : summary,
    }


# ── POST /ask ────────────────────────────────────────────────────────────────
@app.post("/ask")
async def ask(req: AskRequest):
    """
    1. Embed the user's question
    2. Query ChromaDB for top-3 most relevant transcript chunks
    3. Pass chunks as context to Gemini with a strict no-hallucination prompt
    4. Return answer text + citation timestamps
    """
    # Check a video has been loaded
    try:
        collection = chroma_client.get_collection(COLLECTION_NAME)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="No video loaded. Please load a video first."
        )

    # Embed the question
    try:
        q_emb_resp = gemini_client.models.embed_content(
            model   = EMBED_MODEL,
            contents= [req.question],
            config  = types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        q_embedding = q_emb_resp.embeddings[0].values
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Question embedding error: {str(e)}")

    # Retrieve top-3 chunks
    results   = collection.query(
        query_embeddings=[q_embedding],
        n_results=3,
        include=["documents", "metadatas"],
    )
    docs      = results["documents"][0]
    metadatas = results["metadatas"][0]

    # Build context string with timestamps for prompt
    context_parts = []
    citations     = []
    for doc, meta in zip(docs, metadatas):
        ts = fmt_time(meta["start"])
        context_parts.append(f"[{ts}] {doc}")
        citations.append({"timestamp": ts, "seconds": int(meta["start"])})

    context = "\n\n".join(context_parts)

    # Language instruction for the answer
    lang_instruction = (
        "Answer in Hindi (Devanagari script)." if req.language == "hi"
        else "Answer in English."
    )

    # Strict RAG prompt — no hallucination
    prompt = f"""You are VidyaBot, a helpful tutor that answers questions ONLY based on the video transcript below.

RULES (follow strictly):
- Answer ONLY using information present in the transcript context below.
- If the answer is NOT in the transcript, respond with exactly: "This topic is not covered in the video."
- Keep answers concise: 2-4 sentences maximum.
- Do NOT add information from your own knowledge.
- {lang_instruction}

TRANSCRIPT CONTEXT (with timestamps):
{context}

QUESTION: {req.question}

ANSWER:"""

    try:
        response = gemini_client.models.generate_content(
            model   = CHAT_MODEL,
            contents= prompt,
        )
        answer = response.text.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")

    return {
        "answer"   : answer,
        "citations": citations,
    }


# ── POST /transcribe ─────────────────────────────────────────────────────────
@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """
    Receives audio blob (webm) from browser mic.
    Sends to Sarvam Saaras v3 for speech-to-text.
    Returns transcribed text + detected language code (hi or en).
    """
    if not SARVAM_API_KEY:
        raise HTTPException(status_code=500, detail="SARVAM_API_KEY is not set in .env")

    audio_bytes = await audio.read()

    # Determine file extension from content type
    content_type = audio.content_type or "audio/webm"
    suffix       = ".webm" if "webm" in content_type else ".wav"

    # Save to temp file — Sarvam API requires a real file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            response = requests.post(
                "https://api.sarvam.ai/speech-to-text",
                headers={"api-subscription-key": SARVAM_API_KEY},
                files={"file": (f"audio{suffix}", f, content_type)},
                data={
                    "model"          : "saaras:v3",
                    "language_code"  : "unknown",   # auto-detect Hindi vs English
                    "with_timestamps": "false",
                },
                timeout=30,
            )
        response.raise_for_status()
        data       = response.json()
        text       = data.get("transcript", "")
        lang_code  = data.get("language_code", "en-IN")
        lang_short = "hi" if lang_code.startswith("hi") else "en"

    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("message", e.response.text)
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=502, detail=f"Sarvam STT error: {detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        os.unlink(tmp_path)  # always clean up temp file

    return {"text": text, "language": lang_short}


# ── POST /speak ──────────────────────────────────────────────────────────────
@app.post("/speak")
async def speak(req: SpeakRequest):
    """
    Converts answer text to speech using Sarvam Bulbul v3.
    Language-matched: Hindi question → Hindi voice (Anushka)
                      English question → English voice (Arvind)
    Returns base64-encoded WAV audio for browser playback.
    """
    if not SARVAM_API_KEY:
        raise HTTPException(status_code=500, detail="SARVAM_API_KEY is not set in .env")

    # Trim text to 500 chars — Bulbul REST API has a character limit
    text_to_speak = req.text[:500]

    # Select speaker and language based on detected question language
    if req.language == "hi":
        lang_code = "hi-IN"
        speaker   = "anushka"
    else:
        lang_code = "en-IN"
        speaker   = "arvind"

    try:
        response = requests.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type"        : "application/json",
            },
            json={
                "inputs"              : [text_to_speak],
                "target_language_code": lang_code,
                "speaker"             : speaker,
                "model"               : "bulbul:v3",
                "enable_preprocessing": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        data      = response.json()
        audio_b64 = data.get("audios", [""])[0]

    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("message", e.response.text)
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=502, detail=f"Sarvam TTS error: {detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")

    return {"audio_base64": audio_b64, "language": req.language}


# ── GET /health ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0"}
