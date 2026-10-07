"""On-device voice input for the chat composer (local Whisper — free, private, Greek→English)."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from .. import voice

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/status")
def voice_status():
    """Install/model state for the mic button (installed / installing / loading / ready / device)."""
    return voice.status()


@router.post("/enable")
def voice_enable():
    """Turn voice on: install faster-whisper (self-contained) + start loading the model. Background —
    returns immediately; the UI polls /status until ready."""
    voice.ensure_installed()
    voice.ensure_worker()
    return voice.status()


@router.post("/transcribe")
def voice_transcribe(audio: UploadFile = File(...), task: str = Form("")):
    """Transcribe a recorded clip. task='translate' → English (any spoken language, incl. Greek);
    task='transcribe' → keep the spoken language. Sync def → FastAPI threadpools it (off the loop)."""
    data = audio.file.read()
    if not data:
        return {"error": "empty audio"}
    return voice.transcribe(data, filename=audio.filename or "audio.webm", task=task)
