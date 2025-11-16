from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


class DocumentMetadata(BaseModel):
    """All metadata from a retrieved document."""
    content: str  # page_content
    metadata: Dict[str, Any]  # All metadata properties


class ControlGroupSummary(BaseModel):
    """Summary of a control group."""
    id: str
    title: Optional[str] = None
    description: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    control_groups: List[ControlGroupSummary] = Field(default_factory=list)
    controls: List[ControlSummary] = Field(default_factory=list)
    rules: List[RuleSummary] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    documents: List[DocumentMetadata] = Field(default_factory=list)  # All document metadata
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
