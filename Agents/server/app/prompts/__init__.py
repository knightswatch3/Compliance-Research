"""Prompt templates for the compliance agent."""

from app.prompts.cypher_generation import (
    CYPHER_GENERATION_PROMPT,
    get_cypher_generation_prompt,
)
from app.prompts.qa_prompt import qa_prompt

__all__ = [
    "CYPHER_GENERATION_PROMPT",
    "get_cypher_generation_prompt",
    "qa_prompt",
]

