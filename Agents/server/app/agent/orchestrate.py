import os
from typing import Tuple

from dotenv import load_dotenv
from langchain.chains import RetrievalQA
from langchain_google_genai import ChatGoogleGenerativeAI

from app.knowledge.retriever import Neo4jControlRetriever


def load_google_api_key(env_file: str = ".env") -> str:
    """Load Google Gemini API key from the .env file into environment variables."""

    load_dotenv(dotenv_path=env_file)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not found in the .env file!")
    os.environ["GOOGLE_API_KEY"] = api_key
    return api_key


def initialize_agent() -> Tuple[RetrievalQA, Neo4jControlRetriever]:
    """Build and return the retrieval-based QA chain backed by Neo4j.
    
    Returns:
        Tuple of (RetrievalQA chain, Neo4jControlRetriever) so we can
        use the retriever separately to extract controls/rules.
    """

    load_google_api_key()

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.0,
        max_tokens=None,
        timeout=30,  # 30 second timeout for LLM calls
        max_retries=2,
    )

    # Create retriever and pass LLM for query generation
    retriever = Neo4jControlRetriever(top_k=10)
    retriever.llm = llm  # Pass LLM to retriever for Cypher generation

    chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
    )
    return chain, retriever


# For backward compatibility and easier access
def get_retriever() -> Neo4jControlRetriever:
    """Get a retriever instance. Note: This creates a new instance."""
    return Neo4jControlRetriever(top_k=10)
