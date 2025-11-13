import os

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


def initialize_agent() -> RetrievalQA:
    """Build and return the retrieval-based QA chain backed by Neo4j."""

    load_google_api_key()

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
    )

    retriever = Neo4jControlRetriever(top_k=10)

    chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
    )
    return chain
