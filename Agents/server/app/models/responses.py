from typing import Any, List, Optional

from pydantic import BaseModel


class Citation(BaseModel):
    label: str
    snippet: Optional[str] = None
    url: Optional[str] = None


class ControlSummary(BaseModel):
    control_id: str
    title: Optional[str] = None
    group_id: Optional[str] = None


class RuleSummary(BaseModel):
    rule_id: str
    platform: Optional[str] = None
    tool: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation] = []
    controls: List[ControlSummary] = []
    rules: List[RuleSummary] = []
    metadata: dict[str, Any] = {}
