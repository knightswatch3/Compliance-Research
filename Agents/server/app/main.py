from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.agent.orchestrate import initialize_agent
from app.models.requests import ChatRequest
from app.models.responses import ChatResponse

agent = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global agent
    try:
        agent = initialize_agent()
        print("agent initialized:", agent)
        yield
    except Exception as exc:
        print("error initializing agent:", exc)
        raise
    finally:
        print("Shutting down agent and server")
        agent = None


app = FastAPI(title="Compliance Agent API", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Compliance Agent backend is running"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if agent is None:
        raise RuntimeError("Agent is not initialized")

    result = agent.invoke({"query": request.question})
    return ChatResponse(answer=result.get("result", ""))
