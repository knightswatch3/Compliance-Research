"""Conversation history management using Neo4j."""

import logging
from datetime import datetime
from typing import List, Optional

from langchain_neo4j import Neo4jGraph

from app.models.requests import ChatTurn

logger = logging.getLogger(__name__)


class ConversationStore:
    """Manages conversation history in Neo4j."""
    
    def __init__(self, graph: Neo4jGraph):
        """Initialize with a Neo4j graph connection.
        
        Args:
            graph: Neo4jGraph instance for database operations
        """
        self.graph = graph
        self._ensure_schema()
    
    def _ensure_schema(self):
        """Ensure the conversation schema exists in Neo4j."""
        # Create indexes for better performance
        try:
            # Index on session_id for fast lookups
            self.graph.query("""
            CREATE INDEX session_id_index IF NOT EXISTS
            FOR (s:Session) ON (s.session_id)
            """)
            
            # Index on timestamp for ordering
            self.graph.query("""
            CREATE INDEX message_timestamp_index IF NOT EXISTS
            FOR (m:Message) ON (m.timestamp)
            """)
            logger.info("Conversation schema indexes created/verified")
        except Exception as e:
            logger.warning(f"Could not create indexes (they may already exist): {e}")
    
    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None
    ) -> str:
        """Save a message to Neo4j.
        
        Args:
            session_id: Unique session identifier
            role: 'user' or 'assistant'
            content: Message content
            metadata: Optional metadata (e.g., controls, rules, citations)
            
        Returns:
            Message ID
        """
        timestamp = datetime.utcnow().isoformat()
        message_id = f"{session_id}_{timestamp}"
        
        # Create or update session
        self.graph.query("""
        MERGE (s:Session {session_id: $session_id})
        ON CREATE SET s.created_at = $timestamp, s.updated_at = $timestamp
        ON MATCH SET s.updated_at = $timestamp
        """, {"session_id": session_id, "timestamp": timestamp})
        
        # Create message and link to session
        params = {
            "session_id": session_id,
            "message_id": message_id,
            "role": role,
            "content": content,
            "timestamp": timestamp
        }
        
        # Build query with or without metadata
        if metadata:
            params["metadata"] = metadata
            query = """
            MATCH (s:Session {session_id: $session_id})
            CREATE (m:Message {
                message_id: $message_id,
                role: $role,
                content: $content,
                timestamp: $timestamp,
                metadata: $metadata
            })
            CREATE (s)-[:HAS_MESSAGE]->(m)
            RETURN m.message_id AS message_id
            """
        else:
            query = """
            MATCH (s:Session {session_id: $session_id})
            CREATE (m:Message {
                message_id: $message_id,
                role: $role,
                content: $content,
                timestamp: $timestamp
            })
            CREATE (s)-[:HAS_MESSAGE]->(m)
            RETURN m.message_id AS message_id
            """
        
        result = self.graph.query(query, params)
        logger.debug(f"Saved message {message_id} for session {session_id}")
        return message_id
    
    def get_conversation_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[ChatTurn]:
        """Retrieve conversation history for a session.
        
        Args:
            session_id: Session identifier
            limit: Maximum number of messages to retrieve (default: 10)
            
        Returns:
            List of ChatTurn objects in chronological order
        """
        query = """
        MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
        RETURN m.role AS role, m.content AS content
        ORDER BY m.timestamp ASC
        LIMIT $limit
        """
        
        result = self.graph.query(query, {"session_id": session_id, "limit": limit})
        
        # Group consecutive messages by role
        history = []
        current_turn = None
        
        for record in result:
            role = record.get("role")
            content = record.get("content")
            
            if role == "user":
                # Start a new turn
                if current_turn:
                    history.append(current_turn)
                current_turn = ChatTurn(user=content, assistant=None)
            elif role == "assistant" and current_turn:
                # Complete the current turn
                current_turn.assistant = content
                history.append(current_turn)
                current_turn = None
            elif role == "assistant":
                # Assistant message without preceding user message (shouldn't happen, but handle it)
                history.append(ChatTurn(user="", assistant=content))
        
        # Add the last turn if it's incomplete
        if current_turn:
            history.append(current_turn)
        
        logger.info(f"Retrieved {len(history)} conversation turns for session {session_id}")
        return history
    
    def get_recent_messages(
        self,
        session_id: str,
        count: int = 5
    ) -> List[dict]:
        """Get the most recent messages (for context building).
        
        Args:
            session_id: Session identifier
            count: Number of recent messages to retrieve
            
        Returns:
            List of message dicts with role and content
        """
        query = """
        MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
        RETURN m.role AS role, m.content AS content
        ORDER BY m.timestamp DESC
        LIMIT $count
        """
        
        result = self.graph.query(query, {"session_id": session_id, "count": count})
        messages = [
            {"role": record.get("role"), "content": record.get("content")}
            for record in reversed(result)  # Reverse to get chronological order
        ]
        
        return messages

