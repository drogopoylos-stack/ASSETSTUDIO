"""Ask AI — general multi-model chat (OpenRouter) with saved conversations."""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import llm_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


class KeyBody(BaseModel):
    key: str


@router.get("/key")
def key_status():
    return {"has_key": llm_chat.has_key()}


@router.post("/key")
def set_key(body: KeyBody):
    llm_chat.set_key(body.key)
    llm_chat.list_models(force=True)   # refresh the catalogue with the new key
    return {"ok": True, "has_key": llm_chat.has_key()}


@router.get("/models")
def models(force: bool = False):
    return llm_chat.list_models(force=force)


@router.get("/conversations")
def conversations():
    return {"conversations": llm_chat.list_conversations()}


class NewBody(BaseModel):
    model: str = ""
    title: str = ""


@router.post("/conversations")
def new_conversation(body: NewBody):
    return llm_chat.create_conversation(body.model, body.title)


@router.get("/conversations/{cid}")
def get_conversation(cid: str):
    conv = llm_chat.get_conversation(cid)
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return conv


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str):
    return {"ok": llm_chat.delete_conversation(cid)}


class RenameBody(BaseModel):
    title: str


@router.patch("/conversations/{cid}")
def rename_conversation(cid: str, body: RenameBody):
    conv = llm_chat.rename_conversation(cid, body.title)
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return conv


class SendBody(BaseModel):
    message: str
    model: str = ""
    attachments: list = []


@router.post("/conversations/{cid}/send")
def send(cid: str, body: SendBody):
    """Stream the model's reply as newline-delimited JSON: {delta}/{error}/{done}."""
    gen = llm_chat.stream_chat(cid, body.message, body.model, body.attachments)
    return StreamingResponse(gen, media_type="application/x-ndjson")


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Attach an image (→ vision) or a code/text file (→ context) to the chat."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "file too large (max 20 MB)")
    return llm_chat.save_upload(file.filename or "file", data)


@router.get("/file")
def get_file(name: str):
    p = llm_chat.file_path(name)
    if p is None:
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")
