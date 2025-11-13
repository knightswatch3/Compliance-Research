from typing import List, Optional

from pydantic import BaseModel


class ChatTurn(BaseModel):
    user: str
    assistant: Optional[str] = None


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    history: Optional[List[ChatTurn]] = None
