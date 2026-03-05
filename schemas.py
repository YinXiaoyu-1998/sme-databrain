from typing import Any, Optional
from pydantic import BaseModel


class LoadContextRequest(BaseModel):
    fileId: str
    filepath: str
    mimeType: str


class ChatRequest(BaseModel):
    userId: str
    fileId: Optional[str] = None
    chatId: Optional[str] = None
    message: str
    history: Optional[list] = None


class ChunkResult(BaseModel):
    content: str
    metadata: dict[str, Any]
    score: float


class GeneratedFileInfo(BaseModel):
    id: str
    fileType: str
    mimeType: str
    filename: str
    path: str
    size: int = 0
