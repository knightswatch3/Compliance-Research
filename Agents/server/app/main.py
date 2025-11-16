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
from app.models.responses import ChatResponse, ControlSummary, RuleSummary, ControlGroupSummary, Citation, DocumentMetadata
from app.tools.formatter import DocumentFormatter

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

agent = None
retriever = None
conversation_store = None
formatter = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global agent, retriever, conversation_store, formatter
    try:
        agent, retriever = initialize_agent()
        conversation_store = ConversationStore(graph)
        # Initialize formatter with the same LLM from the agent
        from langchain_google_genai import ChatGoogleGenerativeAI
        from dotenv import load_dotenv
        import os
        load_dotenv()
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.0,
            timeout=30,
        )
        formatter = DocumentFormatter(llm=llm)
        logger.info("Agent, retriever, conversation store, and formatter initialized successfully")
        yield
    except Exception as exc:
        logger.error(f"Error initializing agent: {exc}")
        raise
    finally:
        logger.info("Shutting down agent and server")
        agent = None
        retriever = None
        conversation_store = None
        formatter = None


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

    # Step 2: Convert documents to response format (simple - just extract page_content and metadata)
    documents_metadata = [
        DocumentMetadata(
            content=doc.page_content,
            metadata=doc.metadata if doc.metadata is not None else {}
        )
        for doc in retrieved_docs
    ]
    
    # Simple citations
    citations = [
        Citation(
            label=f"Document {i+1}",
            snippet=doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
        )
        for i, doc in enumerate(retrieved_docs)
    ]
    
    logger.info(f"Converted {len(documents_metadata)} documents to response format")
    
    # Step 3: Use formatter tool to format documents and extract information
    if formatter is None:
        raise RuntimeError("Formatter is not initialized")
    
    # Build conversation context for formatter
    context = None
    if history_to_use and len(history_to_use) > 0:
        logger.info(f"Including {len(history_to_use)} previous turns in context for formatter")
        context_parts = []
        for turn in history_to_use[-5:]:  # Only include last 5 turns
            if turn.user:
                context_parts.append(f"User: {turn.user}")
            if turn.assistant:
                context_parts.append(f"Assistant: {turn.assistant}")
        
        if context_parts:
            context = "\n".join(context_parts)
    
    logger.info("Using formatter tool to format documents and extract controls/rules")
    formatted_result = formatter.format_documents(
        documents=retrieved_docs,
        user_query=request.question,
        context=context
    )
    
    # Extract formatted answer and structured data
    answer = formatted_result.get("answer", "")
    control_groups_raw = formatted_result.get("control_groups", [])
    controls_raw = formatted_result.get("controls", [])
    rules_raw = formatted_result.get("rules", [])
    
    # Convert control groups to ControlGroupSummary objects
    control_groups = [
        ControlGroupSummary(
            id=group.get("id", ""),
            title=group.get("title"),
            description=group.get("description")
        )
        for group in control_groups_raw
    ]
    
    # Convert extracted controls to ControlSummary objects
    # controls_raw can be a list of strings (control IDs) or dicts (control objects)
    controls = []
    for ctrl in controls_raw:
        if isinstance(ctrl, dict):
            # Full control object with title and group_id
            controls.append(ControlSummary(
                control_id=ctrl.get("control_id") or ctrl.get("id", ""),
                title=ctrl.get("title"),
                group_id=ctrl.get("group_id")
            ))
        else:
            # Just a control ID string
            controls.append(ControlSummary(
                control_id=str(ctrl),
                title=None,
                group_id=None
            ))
    
    # Convert extracted rules to RuleSummary objects
    # rules_raw can be a list of strings (rule IDs) or dicts (rule objects)
    rules = []
    for rule in rules_raw:
        if isinstance(rule, dict):
            # Full rule object with platform and tool
            rules.append(RuleSummary(
                rule_id=rule.get("rule_id") or rule.get("id", ""),
                platform=rule.get("platform"),
                tool=rule.get("tool")
            ))
        else:
            # Just a rule ID string
            rules.append(RuleSummary(
                rule_id=str(rule),
                platform=None,
                tool=None
            ))
    
    logger.info(f"Formatter extracted {len(control_groups)} control groups, {len(controls)} controls, and {len(rules)} rules")
    logger.info(f"Generated formatted answer (length: {len(answer)} chars)")
    
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
                "control_groups_count": len(control_groups),
                "controls_count": len(controls),
                "rules_count": len(rules),
                "retrieved_docs_count": len(retrieved_docs)
            }
        )
        logger.info(f"Saved conversation to Neo4j for session {session_id}")
    except Exception as e:
        logger.warning(f"Could not save conversation to Neo4j: {e}")
    
    # Step 5: Return response
    return ChatResponse(
        answer=answer,
        control_groups=control_groups,
        controls=controls,
        rules=rules,
        citations=citations,
        metadata={
            "retrieved_docs_count": len(retrieved_docs),
            "query": request.question,
            "session_id": session_id,  # Return session_id so client can use it for next request
        },
    )
