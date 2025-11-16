import logging
import os
import re

from dotenv import load_dotenv
from langchain.schema import BaseRetriever, Document
from langchain_neo4j import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from app.knowledge.schema import get_neo4j_schema, get_fallback_schema
from app.knowledge.cypher_queries import (
    get_control_group_exact_match_query,
    get_control_group_contains_match_query,
    get_control_group_word_match_query,
    get_control_group_all_query,
    get_control_dependency_query,
    get_control_dependency_fallback_query,
    get_control_search_query,
)
from app.prompts import get_cypher_generation_prompt
load_dotenv()

logger = logging.getLogger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
    raise RuntimeError("Neo4j credentials are not fully configured in the environment")


graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD)


class Neo4jControlRetriever(BaseRetriever):
    """Simple keyword-based retriever for controls stored in Neo4j."""

    top_k: int = 5
    llm: ChatGoogleGenerativeAI = None
    schema: str = None

    def _generate_cypher_with_llm(self, user_query: str) -> str:
        """Use LLM to generate Cypher query from natural language."""
        if self.llm is None:
            raise ValueError("LLM not initialized for query generation")
        
        if self.schema is None:
            logger.info("Fetching Neo4j schema...")
            try:
                self.schema = get_neo4j_schema(graph)
                logger.info("Schema fetched and cached")
            except Exception as e:
                logger.warning(f"Failed to fetch schema: {e}. Using fallback schema.")
                # Use fallback schema
                self.schema = get_fallback_schema()
        
        prompt = get_cypher_generation_prompt(
            schema=self.schema,
            user_query=user_query
        )

        try:
            logger.info("Calling LLM to generate Cypher query...")
            response = self.llm.invoke(prompt)
            cypher = response.content.strip()
            
            # Clean up if LLM added markdown code blocks
            if cypher.startswith("```"):
                lines = cypher.split("\n")
                cypher = "\n".join(lines[1:-1])  # Remove first and last line
            if cypher.startswith("cypher"):
                cypher = cypher[6:].strip()
            
            logger.info(f"Generated Cypher query (first 300 chars): {cypher[:300]}...")
            logger.debug(f"Full generated Cypher query: {cypher}")
            return cypher
        except Exception as e:
            logger.error(f"Error generating Cypher query: {e}", exc_info=True)
            raise

    def _get_relevant_documents(self, query: str) -> list[Document]:
        logger.info(f"Retrieving documents for query: '{query}' (top_k={self.top_k})")
        
        # Detect if user is asking for "all" items - remove limit in that case
        is_all_query = any(word in query.lower() for word in ["all", "every", "list all", "show all", "get all", "count"])
        effective_top_k = None if is_all_query else self.top_k
        
        if is_all_query:
            logger.info("Detected 'all' query - removing LIMIT constraint")
        
        # Use LLM to generate Cypher query
        if self.llm is not None:
            try:
                cypher = self._generate_cypher_with_llm(query)
                logger.info("Executing generated Cypher query...")
                logger.debug(f"Full Cypher query to execute: {cypher}")
                # Execute the generated query
                # Prepare parameters - include both the full query and extracted values
                params = {
                    "query": query,
                }
                # Only add top_k if not an "all" query
                if effective_top_k is not None:
                    params["top_k"] = effective_top_k
                # Extract control ID if present (for queries that might use $control_id parameter)
                control_id_match = re.search(r'\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b', query.upper())
                if control_id_match:
                    params["control_id"] = control_id_match.group(1)
                
                logger.debug(f"Query parameters: {params}")
                
                # If it's an "all" query, remove LIMIT clause from the generated query
                if is_all_query and "LIMIT" in cypher.upper():
                    # Remove LIMIT clause (case-insensitive)
                    cypher = re.sub(r'\s+LIMIT\s+\$?top_k\s*$', '', cypher, flags=re.IGNORECASE)
                    cypher = re.sub(r'\s+LIMIT\s+\d+\s*$', '', cypher, flags=re.IGNORECASE)
                    logger.info("Removed LIMIT clause for 'all' query")
                    logger.debug(f"Modified Cypher: {cypher}")
                
                records = graph.query(cypher, params)
                logger.info(f"Query executed successfully, returned {len(records)} records")
                if len(records) == 0:
                    logger.warning("No records returned from Cypher query. The query might be incorrect or no relationships exist.")
                    logger.warning(f"Query was: {cypher}")
                    logger.warning(f"Parameters were: {params}")
                    # If it's a control group query and we got 0 results, try the fallback
                    if "control group" in query.lower() or "group" in query.lower():
                        logger.info("LLM query returned 0 results for control group query. Trying fallback...")
                        # Extract group name and try fallback query
                        group_name = None
                        # Extract text after "with name" - capture everything to end of string
                        # Use greedy match to capture full multi-word names like "Incident Response"
                        name_match = re.search(r'(?:with name|named|called)\s+(.+)$', query.lower())
                        if name_match:
                            group_name = name_match.group(1).strip()
                        
                        # If not found, try common group names
                        if not group_name:
                            common_groups = [
                                "access control", "audit and accountability", "identification and authentication",
                                "system and communications protection", "configuration management",
                                "incident response", "maintenance", "media protection", "personnel security",
                                "physical and environmental protection", "program management", "recovery",
                                "risk assessment", "security assessment and authorization", "supply chain risk management",
                                "system and information integrity", "awareness and training",
                                "assessment authorization and monitoring", "contingency planning"
                            ]
                            # Sort by length (longest first) to match "incident response" before just "incident"
                            common_groups.sort(key=len, reverse=True)
                            for group in common_groups:
                                if group in query.lower():
                                    group_name = group
                                    break
                        
                        if group_name:
                            logger.info(f"Trying fallback with extracted group name: '{group_name}'")
                            
                            # Try exact match first
                            fallback_cypher = get_control_group_exact_match_query()
                            records = graph.query(fallback_cypher, {"group_name": group_name, "top_k": self.top_k})
                            logger.info(f"Fallback exact match returned {len(records)} records")
                            
                            # If no exact match, try CONTAINS
                            if len(records) == 0:
                                fallback_cypher = get_control_group_contains_match_query()
                                records = graph.query(fallback_cypher, {"group_name": group_name, "top_k": self.top_k})
                                logger.info(f"Fallback CONTAINS match returned {len(records)} records")
            except Exception as e:
                logger.warning(f"LLM query generation failed: {e}. Falling back to default query.", exc_info=True)
                # Fallback queries based on query type
                if "control group" in query.lower() or "group" in query.lower():
                    # Fallback for control group queries
                    logger.info("Using control group fallback query")
                    
                    # Check if it's an "all" query
                    if is_all_query:
                        # Query for ALL control groups
                        logger.info("Fallback: Querying for ALL control groups")
                        cypher = get_control_group_all_query()
                        records = graph.query(cypher, {})
                        logger.info(f"Control group 'all' query returned {len(records)} records")
                    else:
                        # Query for specific control group
                        # Extract the group name from the query (e.g., "access control" from "Tell me about the control group with name access control")
                        group_name = None
                        
                        # Try to extract text after "name" or "with name" - capture everything to end of string
                        # Use greedy match to capture full multi-word names like "Incident Response"
                        name_match = re.search(r'(?:with name|named|called)\s+(.+)$', query.lower())
                        if name_match:
                            group_name = name_match.group(1).strip()
                        
                        # If not found, try to extract common control group names
                        if not group_name:
                            common_groups = [
                                "access control", "audit and accountability", "identification and authentication",
                                "system and communications protection", "configuration management",
                                "incident response", "maintenance", "media protection", "personnel security",
                                "physical and environmental protection", "program management", "recovery",
                                "risk assessment", "security assessment and authorization", "supply chain risk management",
                                "system and information integrity", "awareness and training",
                                "assessment authorization and monitoring", "contingency planning"
                            ]
                            # Sort by length (longest first) to match "incident response" before just "incident"
                            common_groups.sort(key=len, reverse=True)
                            for group in common_groups:
                                if group in query.lower():
                                    group_name = group
                                    break
                        
                        # If still not found, try to extract 2-3 letter codes (AC, AU, etc.)
                        if not group_name:
                            code_match = re.search(r'\b([A-Z]{2,3})\b', query.upper())
                            if code_match:
                                group_name = code_match.group(1)
                        
                        # Fallback: use the last few words of the query
                        if not group_name:
                            words = query.lower().split()
                            # Take last 2-3 words that aren't common stop words
                            stop_words = {"the", "a", "an", "about", "with", "name", "group", "control", "tell", "me", "show", "list"}
                            relevant_words = [w for w in words if w not in stop_words]
                            if relevant_words:
                                group_name = " ".join(relevant_words[-2:])  # Take last 2 words
                        
                        # Final fallback: use full query
                        if not group_name:
                            group_name = query
                        
                        logger.info(f"Extracted group name: '{group_name}' from query: '{query}'")
                        
                        # Try multiple matching strategies
                        # Strategy 1: Exact match (case-insensitive)
                        cypher = get_control_group_exact_match_query()
                        records = graph.query(cypher, {"group_name": group_name, "top_k": self.top_k})
                        logger.info(f"Exact match query returned {len(records)} records")
                        
                        # Strategy 2: If no exact match, try CONTAINS
                        if len(records) == 0:
                            logger.info("Trying CONTAINS match...")
                            cypher = get_control_group_contains_match_query()
                            records = graph.query(cypher, {"group_name": group_name, "top_k": self.top_k})
                            logger.info(f"CONTAINS match query returned {len(records)} records")
                        
                        # Strategy 3: Try matching individual words
                        if len(records) == 0 and " " in group_name:
                            logger.info("Trying word-by-word match...")
                            words = group_name.split()
                            word_conditions = " OR ".join([f"toLower(c.title) CONTAINS toLower('{word}')" for word in words])
                            cypher = get_control_group_word_match_query(word_conditions)
                            records = graph.query(cypher, {"group_name": group_name, "top_k": self.top_k})
                            logger.info(f"Word-by-word match query returned {len(records)} records")
                        
                        if len(records) == 0:
                            logger.warning(f"No ControlGroup found matching '{group_name}' after all strategies")
                elif "depend" in query.lower() or "dependent" in query.lower():
                    # Try a dependency-specific fallback
                    # Extract control ID from query (e.g., "AC-1" from "What are the controls that depend on AC-1?")
                    control_id_match = re.search(r'\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b', query.upper())
                    if control_id_match:
                        control_id = control_id_match.group(1)
                        logger.info(f"Using dependency fallback query for control: {control_id}")
                        cypher = get_control_dependency_query(control_id)
                        logger.debug(f"Fallback Cypher query: {cypher}")
                        records = graph.query(cypher, {"query": query, "top_k": self.top_k})
                        logger.info(f"Fallback query returned {len(records)} records")
                    else:
                        # Fallback to parameterized query
                        logger.info("Using dependency fallback query with parameter")
                        # Extract control ID pattern from query
                        control_id_match = re.search(r'\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b', query.upper())
                        if control_id_match:
                            control_id = control_id_match.group(1)
                            cypher = get_control_dependency_query(control_id)
                            logger.debug(f"Fallback Cypher query: {cypher}")
                            records = graph.query(cypher, {"query": query, "top_k": self.top_k})
                            logger.info(f"Fallback query returned {len(records)} records")
                        else:
                            cypher = get_control_dependency_fallback_query()
                            logger.debug(f"Fallback Cypher query: {cypher}")
                            records = graph.query(cypher, {"query": query, "top_k": self.top_k})
                            logger.info(f"Fallback query returned {len(records)} records")
                else:
                    # Standard fallback
                    cypher = get_control_search_query()
                records = graph.query(cypher, {"query": query, "top_k": self.top_k})
        else:
            # Default query if LLM not available
            if is_all_query and ("control group" in query.lower() or "group" in query.lower()):
                # Query for all control groups
                cypher = get_control_group_all_query()
                records = graph.query(cypher, {})
            else:
                cypher = get_control_search_query()
                records = graph.query(cypher, {"query": query, "top_k": self.top_k})
        
        logger.info(f"Cypher query returned {len(records)} records")
        
        documents: list[Document] = []
        for record in records:
            # The LLM should always return "c" and "rules" per our prompt requirements
            # But we'll still check for flexibility
            node = record.get("c")
            if node is None:
                # Fallback: try to find any node-like dict
                for key, value in record.items():
                    if isinstance(value, dict) and ("control_id" in value or "id" in value):
                        node = value
                        logger.warning(f"Expected 'c' but found '{key}' in record. Using it anyway.")
                        break
                if node is None:
                    logger.warning(f"Could not find node in record. Keys: {list(record.keys())}")
                    continue
            
            # Get rules - should always be "rules" per our prompt
            rules = record.get("rules", [])
            if not isinstance(rules, list):
                rules = []
            
            # Get controls - returned for control group queries
            controls = record.get("controls", [])
            if not isinstance(controls, list):
                controls = []
            
            # Determine if this is a Control or ControlGroup
            control_id = node.get("control_id")
            group_id = node.get("id")  # ControlGroup uses "id"
            
            if control_id:
                # It's a Control node
                logger.debug(f"Processing control: {control_id}")
                metadata = {
                    "control_id": control_id,
                    "title": node.get("title"),
                    "group_id": node.get("control_group"),
                    "rules": [
                        {
                            "rule_id": rule.get("rule_id") if isinstance(rule, dict) else str(rule),
                            "text": rule.get("text") if isinstance(rule, dict) else None,
                            "platform": rule.get("platform") if isinstance(rule, dict) else None,
                        }
                        for rule in rules
                        if rule
                    ],
                }
                content = "\n\n".join(filter(None, [node.get("title"), node.get("description")]))
                documents.append(Document(page_content=content, metadata=metadata))
                logger.debug(f"Added document for control {control_id} with {len(metadata['rules'])} rules")
            elif group_id:
                # It's a ControlGroup node
                logger.debug(f"Processing control group: {group_id}")
                # Extract control information from controls list
                control_list = []
                for control in controls:
                    if isinstance(control, dict) and control.get("control_id"):
                        control_list.append({
                            "control_id": control.get("control_id"),
                            "title": control.get("title"),
                        })
                
                metadata = {
                    "group_id": group_id,
                    "title": node.get("title"),
                    "description": node.get("description"),
                    "purpose": node.get("purpose"),
                    "controls": control_list,  # Add first 10 controls to metadata
                }
                content = "\n\n".join(filter(None, [node.get("title"), node.get("description"), node.get("purpose")]))
                documents.append(Document(page_content=content, metadata=metadata))
                logger.debug(f"Added document for control group {group_id} with {len(control_list)} controls")
            else:
                logger.warning(f"Unknown node type in record: {node}")
        
        logger.info(f"Returning {len(documents)} documents")
        return documents
