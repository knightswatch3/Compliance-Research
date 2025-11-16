# Prompt Templates

This directory contains all prompt templates used by the compliance agent.

## Structure

- `cypher_generation.py` - Prompt for generating Cypher queries from natural language
- `__init__.py` - Exports all prompt functions for easy importing

## Adding New Prompts

1. Create a new file (e.g., `rag_prompt.py`)
2. Define your prompt template as a constant or function
3. Export it in `__init__.py`
4. Import and use it in your code

## Example

```python
# app/prompts/rag_prompt.py
RAG_PROMPT = """You are a compliance expert...
{context}
{question}
"""

def get_rag_prompt(context: str, question: str) -> str:
    return RAG_PROMPT.format(context=context, question=question)
```

Then import:
```python
from app.prompts import get_rag_prompt
```

