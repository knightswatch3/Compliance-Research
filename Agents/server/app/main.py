import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from fastapi import FastAPI

from app.agent.orchestrate import initialize_agent
from app.knowledge.conversation import ConversationStore
from app.knowledge.retriever import graph
from app.models.requests import ChatRequest
from app.models.responses import ChatResponse, ControlSummary, RuleSummary, Citation

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

agent = None
retriever = None
conversation_store = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global agent, retriever, conversation_store
    try:
        agent, retriever = initialize_agent()
        conversation_store = ConversationStore(graph)
        logger.info("Agent, retriever, and conversation store initialized successfully")
        yield
    except Exception as exc:
        logger.error(f"Error initializing agent: {exc}")
        raise
    finally:
        logger.info("Shutting down agent and server")
        agent = None
        retriever = None
        conversation_store = None


app = FastAPI(title="Compliance Agent API", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Compliance Agent backend is running"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if agent is None or retriever is None or conversation_store is None:
        raise RuntimeError("Agent is not initialized")

    # Generate or use provided session_id
    # If no session_id provided, create a new unique session ID
    if request.session_id:
        session_id = request.session_id
        logger.info(f"Using provided session_id: {session_id}")
    else:
        session_id = f"session_{uuid.uuid4().hex[:12]}"  # Shorter, more readable UUID
        logger.info(f"Created new session_id: {session_id}")
    
    # Step 0: Get conversation history from Neo4j
    conversation_history = []
    try:
        conversation_history = conversation_store.get_conversation_history(
            session_id=session_id,
            limit=10
        )
        if conversation_history:
            logger.info(f"Retrieved {len(conversation_history)} previous turns from Neo4j for session {session_id}")
        else:
            logger.info(f"No previous conversation found for session {session_id} (new session)")
    except Exception as e:
        logger.warning(f"Could not retrieve conversation history: {e}")
    
    # Use provided history if available, otherwise use Neo4j history
    history_to_use = request.history if request.history else conversation_history

    # Step 1: Retrieve relevant documents from Neo4j
    logger.info(f"Retrieving documents for query: {request.question}")
    retrieved_docs = retriever.get_relevant_documents(request.question)
    logger.info(f"Retrieved {len(retrieved_docs)} documents")

    # Step 2: Extract controls and rules from retrieved documents
    controls = []
    rules = []
    citations = []
    
    for doc in retrieved_docs:
        metadata = doc.metadata
        
        # Extract control information
        if "control_id" in metadata and metadata["control_id"]:
            control = ControlSummary(
                control_id=metadata["control_id"],
                title=metadata.get("title"),
                group_id=metadata.get("group_id"),
            )
            # Avoid duplicates
            if not any(c.control_id == control.control_id for c in controls):
                controls.append(control)
            
            # Add citation
            citation = Citation(
                label=metadata["control_id"],
                snippet=doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            )
            citations.append(citation)
        
        # Extract control group information
        elif "group_id" in metadata and metadata["group_id"]:
            # Extract controls from control group metadata (first 10 controls)
            if "controls" in metadata and metadata["controls"]:
                for control_data in metadata["controls"]:
                    if isinstance(control_data, dict) and control_data.get("control_id"):
                        control = ControlSummary(
                            control_id=control_data["control_id"],
                            title=control_data.get("title"),
                            group_id=metadata["group_id"],
                        )
                        # Avoid duplicates
                        if not any(c.control_id == control.control_id for c in controls):
                            controls.append(control)
            
            # Add citation for control group
            citation = Citation(
                label=metadata["group_id"],
                snippet=doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            )
            citations.append(citation)
        
        # Extract rules from metadata
        if "rules" in metadata and metadata["rules"]:
            for rule_data in metadata["rules"]:
                if rule_data and "rule_id" in rule_data:
                    rule = RuleSummary(
                        rule_id=rule_data["rule_id"],
                        platform=rule_data.get("platform"),
                        tool=rule_data.get("tool"),
                    )
                    # Avoid duplicates
                    if not any(r.rule_id == rule.rule_id for r in rules):
                        rules.append(rule)
    
    logger.info(f"Extracted {len(controls)} controls and {len(rules)} rules")
    
    # Step 3: Get answer from RAG chain
    logger.info("Invoking RAG chain to generate answer")
    
    # Format conversation history if provided
    query_with_context = request.question
    if history_to_use and len(history_to_use) > 0:
        logger.info(f"Including {len(history_to_use)} previous turns in context")
        # Build conversation context
        context_parts = []
        for turn in history_to_use[-5:]:  # Only include last 5 turns to avoid token limits
            if turn.user:
                context_parts.append(f"User: {turn.user}")
            if turn.assistant:
                context_parts.append(f"Assistant: {turn.assistant}")
        
        if context_parts:
            conversation_context = "\n".join(context_parts)
            query_with_context = f"""Previous conversation:
{conversation_context}

Current question: {request.question}"""
            logger.debug(f"Query with context: {query_with_context[:200]}...")
    
    result = agent.invoke({"query": query_with_context})
    answer = result.get("result", "")
    
    logger.info(f"Generated answer (length: {len(answer)} chars)")
    
    # Step 4: Save conversation to Neo4j
    try:
        # Save user message
        conversation_store.save_message(
            session_id=session_id,
            role="user",
            content=request.question
        )
        
        # Save assistant response with metadata
        conversation_store.save_message(
            session_id=session_id,
            role="assistant",
            content=answer,
            metadata={
                "controls_count": len(controls),
                "rules_count": len(rules),
                "retrieved_docs_count": len(retrieved_docs)
            }
        )
        logger.info(f"Saved conversation to Neo4j for session {session_id}")
    except Exception as e:
        logger.warning(f"Could not save conversation to Neo4j: {e}")
    
    # Step 5: Return response with extracted information
    return ChatResponse(
        answer=answer,
        controls=controls,
        rules=rules,
        citations=citations,
        metadata={
            "retrieved_docs_count": len(retrieved_docs),
            "query": request.question,
            "session_id": session_id,  # Return session_id so client can use it for next request
        },
    )
