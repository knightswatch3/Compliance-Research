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
    get_control_rules_query,
    get_control_all_query,
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
        is_all_query = any(word in query.lower() for word in ["all", "every", "list all", "show all", "get all", "count", "get me all"])
        # Detect if user is asking for "all controls" from a control group
        is_all_controls_query = is_all_query and (
            "all the controls" in query.lower() or 
            "all controls" in query.lower() or 
            "every control" in query.lower() or
            "get me all the controls" in query.lower() or
            "get all the controls" in query.lower()
        )
        effective_top_k = None if is_all_query else self.top_k
        
        if is_all_query:
            logger.info("Detected 'all' query - removing LIMIT constraint")
        
        # Check if this is an "all control groups" query BEFORE LLM generation
        # This allows us to query directly without LLM interpretation
        # BUT NOT if asking for controls IN a group (that's handled by is_all_controls_from_group_query)
        is_all_control_groups_query = (is_all_query and 
                                       ("control group" in query.lower() or "group" in query.lower()) and
                                       not ("controls" in query.lower() and ("in" in query.lower() or "from" in query.lower() or "under" in query.lower())))
        
        # Check if this is an "all controls from group" query BEFORE LLM generation
        # Pattern: "all the controls from under the group named [name]" or "all controls from group [name]"
        # Also check for variations like "get me all the controls from under the group named"
        is_all_controls_from_group_query = is_all_controls_query and ("from" in query.lower() or "under" in query.lower() or "in" in query.lower()) and ("group" in query.lower() or "control group" in query.lower())
        extracted_group_name_for_controls = None
        
        logger.info(f"Query detection - is_all_query: {is_all_query}, is_all_controls_query: {is_all_controls_query}, is_all_controls_from_group_query: {is_all_controls_from_group_query}")
        
        if is_all_controls_from_group_query:
            logger.info("Detected 'all controls from/in group' query pattern. Extracting group name...")
            logger.info(f"Query text: '{query}'")
            # Extract group name from patterns like:
            # "all the controls from under the group named Contingency Planning"
            # "all controls from group named [name]"
            # "all controls from the group [name]"
            # "all controls in the control group named [name]"
            # Use greedy match to capture full multi-word names - match until end or stop words
            
            # Try pattern for "in the control group named [name]" FIRST (most specific)
            match3 = re.search(r'in\s+(?:the\s+)?control\s+group\s+named\s+(.+?)(?:please|\.|\?|$)', query.lower(), re.DOTALL)
            if match3:
                extracted_group_name_for_controls = match3.group(1).strip()
                extracted_group_name_for_controls = re.sub(r'\s*(please|\.|\?)+.*$', '', extracted_group_name_for_controls).strip()
                logger.info(f"Matched pattern 3 (in control group named): '{extracted_group_name_for_controls}'")
            
            if not extracted_group_name_for_controls:
                # Pattern for "in the group named [name]"
                match4 = re.search(r'in\s+(?:the\s+)?group\s+named\s+(.+?)(?:please|\.|\?|$)', query.lower(), re.DOTALL)
                if match4:
                    extracted_group_name_for_controls = match4.group(1).strip()
                    extracted_group_name_for_controls = re.sub(r'\s*(please|\.|\?)+.*$', '', extracted_group_name_for_controls).strip()
                    logger.info(f"Matched pattern 4 (in group named): '{extracted_group_name_for_controls}'")
            
            if not extracted_group_name_for_controls:
                # Pattern for "group named [name]" (general)
                match1 = re.search(r'group\s+named\s+(.+?)(?:please|\.|\?|$)', query.lower(), re.DOTALL)
                if match1:
                    extracted_group_name_for_controls = match1.group(1).strip()
                    # If it captured too much (includes "please" or punctuation), trim it
                    extracted_group_name_for_controls = re.sub(r'\s*(please|\.|\?)+.*$', '', extracted_group_name_for_controls).strip()
                    logger.info(f"Matched pattern 1 (group named): '{extracted_group_name_for_controls}'")
            
            if not extracted_group_name_for_controls:
                # Pattern for "from [the] group [named] [name]"
                match2 = re.search(r'from\s+(?:under\s+)?(?:the\s+)?group\s+(?:named\s+)?(.+?)(?:please|\.|\?|$)', query.lower(), re.DOTALL)
                if match2:
                    extracted_group_name_for_controls = match2.group(1).strip()
                    # If it captured too much, trim it
                    extracted_group_name_for_controls = re.sub(r'\s*(please|\.|\?)+.*$', '', extracted_group_name_for_controls).strip()
                    logger.info(f"Matched pattern 2 (from group): '{extracted_group_name_for_controls}'")
            
            if extracted_group_name_for_controls:
                # Clean up - remove trailing words
                extracted_group_name_for_controls = re.sub(r'\s+(please|\.|\?)$', '', extracted_group_name_for_controls).strip()
                logger.info(f"Final extracted group name for controls query: '{extracted_group_name_for_controls}'")
            else:
                logger.warning(f"Failed to extract group name from query: '{query}'")
        
        # Check if this is a "controls that depend on [control]" query BEFORE LLM generation
        # Pattern: "controls that depend on AC-1", "dependent controls for AC-1", etc.
        is_dependency_query = ("depend" in query.lower() or "dependent" in query.lower()) and ("control" in query.lower())
        extracted_control_id_for_dependency = None
        
        if is_dependency_query:
            logger.info("Detected 'controls that depend on' query pattern. Extracting control ID/name...")
            # Try to extract control ID first (e.g., "AC-1", "AU-2")
            control_id_match = re.search(r'\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b', query.upper())
            if control_id_match:
                extracted_control_id_for_dependency = control_id_match.group(1)
                logger.info(f"Pre-extracted control ID for dependency query: '{extracted_control_id_for_dependency}'")
            else:
                # Try to extract control name (e.g., "Access Control", "Audit Policy")
                # Look for patterns like "depend on [name]", "dependent on [name]", "depend on the control [name]"
                patterns = [
                    r'depend(?:ent)?\s+on\s+(?:the\s+)?(?:control\s+)?(.+?)(?:\s+control|\.|\?|$)',
                    r'depend(?:ent)?\s+on\s+(?:control\s+)?(.+?)(?:\s+control|\.|\?|$)',
                    r'controls?\s+that\s+depend\s+on\s+(?:the\s+)?(?:control\s+)?(.+?)(?:\s+control|\.|\?|$)',
                ]
                for pattern in patterns:
                    match = re.search(pattern, query.lower(), re.DOTALL)
                    if match:
                        extracted_control_id_for_dependency = match.group(1).strip()
                        # Clean up
                        extracted_control_id_for_dependency = re.sub(r'\s+(control|\.|\?)$', '', extracted_control_id_for_dependency).strip()
                        if extracted_control_id_for_dependency:
                            logger.info(f"Pre-extracted control name for dependency query: '{extracted_control_id_for_dependency}'")
                            break
        
        # Check if this is a "rules inside [control name]" query BEFORE LLM generation
        # This allows us to extract the control name directly and query for it
        is_rules_inside_query = ("rules" in query.lower() or "riles" in query.lower()) and ("inside" in query.lower() or "in" in query.lower())
        extracted_control_name = None
        
        if is_rules_inside_query:
            logger.info("Detected 'rules inside control' query pattern. Extracting control name...")
            # Extract control name using same patterns as fallback
            match1 = re.search(r'get\s+(?:me\s+)?(?:the\s+)?(?:riles|rules)\s+inside\s+(?:the\s+)?(.+?)(?:\s+control\s+(?:you|listed|above)|\s+control$|you\s+listed|listed|above|$)', query.lower(), re.DOTALL)
            if match1:
                extracted_control_name = match1.group(1).strip()
            
            if not extracted_control_name:
                match2 = re.search(r'(?:riles|rules)\s+inside\s+(?:the\s+)?(.+?)(?:\s+control\s+(?:you|listed|above)|\s+control$|you\s+listed|listed|above)', query.lower(), re.DOTALL)
                if match2:
                    extracted_control_name = match2.group(1).strip()
            
            if not extracted_control_name:
                match3 = re.search(r'(?:riles|rules)\s+in\s+(?:the\s+)?(.+?)(?:\s+control|you|listed|above|$)', query.lower(), re.DOTALL)
                if match3:
                    extracted_control_name = match3.group(1).strip()
            
            if extracted_control_name:
                extracted_control_name = re.sub(r'\s+(control|you|listed|above|that|\.|\?)$', '', extracted_control_name).strip()
                extracted_control_name = re.sub(r'^the\s+', '', extracted_control_name).strip()
                logger.info(f"Pre-extracted control name: '{extracted_control_name}'")
        
        # Initialize records to None - will be set by one of the query paths
        records = None
        
        # Check more specific queries FIRST (controls in group is more specific than all groups)
        # If this is an "all controls from group" query, use it directly
        if is_all_controls_from_group_query and extracted_group_name_for_controls:
            logger.info(f"Using direct query for ALL controls from group: '{extracted_group_name_for_controls}'")
            logger.info(f"Original user query: '{query}'")
            # Try exact match first
            cypher = get_control_group_exact_match_query(return_all_controls=True)
            logger.info(f"Executing Cypher query:\n{cypher}")
            logger.info(f"Query parameters: group_name='{extracted_group_name_for_controls}', top_k={self.top_k}")
            records = graph.query(cypher, {"group_name": extracted_group_name_for_controls, "top_k": self.top_k})
            logger.info(f"Direct 'all controls from group' query returned {len(records)} records")
            
            # Log what we got back - DETAILED
            if records:
                logger.info(f"Query returned {len(records)} record(s)")
                for i, record in enumerate(records[:1]):  # Log first record
                    logger.info(f"Record {i} keys: {list(record.keys())}")
                    logger.info(f"Record {i} full content: {record}")
                    if "controls" in record:
                        controls_raw = record.get('controls', [])
                        logger.info(f"Controls in record: {len(controls_raw)} controls")
                        logger.info(f"Controls type: {type(controls_raw)}")
                        if controls_raw:
                            logger.info(f"First control type: {type(controls_raw[0])}")
                            logger.info(f"First control value: {controls_raw[0]}")
                            # Try to access properties
                            if hasattr(controls_raw[0], '__dict__'):
                                logger.info(f"First control __dict__: {controls_raw[0].__dict__}")
                            if isinstance(controls_raw[0], dict):
                                logger.info(f"First control dict keys: {list(controls_raw[0].keys())}")
                    if "c" in record:
                        node_raw = record.get('c')
                        logger.info(f"ControlGroup node type: {type(node_raw)}")
                        logger.info(f"ControlGroup node: {node_raw}")
            
            # If no exact match, try CONTAINS
            if len(records) == 0:
                logger.info("Trying CONTAINS match for group...")
                cypher = get_control_group_contains_match_query(return_all_controls=True)
                records = graph.query(cypher, {"group_name": extracted_group_name_for_controls, "top_k": self.top_k})
                logger.info(f"CONTAINS match returned {len(records)} records")
        elif is_all_controls_from_group_query and not extracted_group_name_for_controls:
            logger.warning(f"Detected 'all controls from group' query but failed to extract group name from: '{query}'")
        # If this is an "all control groups" query, use it directly
        # This bypasses LLM generation for this specific pattern to ensure accuracy
        elif is_all_control_groups_query:
            logger.info("Using direct query for ALL control groups")
            cypher = get_control_group_all_query()
            records = graph.query(cypher, {})
            logger.info(f"Direct 'all control groups' query returned {len(records)} records")
        # If this is an "all controls" query (not from a group), use it directly
        elif is_all_controls_query and not is_all_controls_from_group_query:
            logger.info("Using direct query for ALL controls")
            cypher = get_control_all_query()
            records = graph.query(cypher, {})
            logger.info(f"Direct 'all controls' query returned {len(records)} records")
        # If this is a dependency query, use it directly
        # This bypasses LLM generation for this specific pattern to ensure accuracy
        if records is None and is_dependency_query and extracted_control_id_for_dependency:
            logger.info(f"Using direct query for dependent controls of: '{extracted_control_id_for_dependency}'")
            # Check if it's a control ID (e.g., "AC-1") or a control name
            if re.match(r'^[A-Z]{2,3}-\d+(?:\(\d+\))?$', extracted_control_id_for_dependency):
                # It's a control ID
                cypher = get_control_dependency_query(extracted_control_id_for_dependency)
                records = graph.query(cypher, {"top_k": self.top_k})
                logger.info(f"Direct dependency query returned {len(records)} records")
            else:
                # It's a control name - need to find the control first, then get dependencies
                # For now, fall through to LLM generation or use a search query
                logger.warning(f"Control name '{extracted_control_id_for_dependency}' provided for dependency query. Falling back to LLM generation.")
                # We'll let it fall through to LLM generation
                records = None
        # If we extracted a control name for "rules inside" query, use it directly
        # This bypasses LLM generation for this specific pattern to ensure accuracy
        elif records is None and extracted_control_name:
            logger.info(f"Using direct query for control: '{extracted_control_name}'")
            cypher = get_control_rules_query(extracted_control_name)
            records = graph.query(cypher, {"control_name": extracted_control_name, "top_k": self.top_k})
            logger.info(f"Direct control rules query returned {len(records)} records")
            
            # If no results, try with shorter name
            if len(records) == 0 and len(extracted_control_name.split()) > 3:
                words = extracted_control_name.split()
                shorter_name = " ".join(words[-4:])
                logger.info(f"Trying shorter control name: '{shorter_name}'")
                cypher = get_control_rules_query(shorter_name)
                records = graph.query(cypher, {"control_name": shorter_name, "top_k": self.top_k})
                logger.info(f"Shorter name query returned {len(records)} records")
        # Use LLM to generate Cypher query
        elif self.llm is not None:
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
                    
                    # Check if query is asking about "rules inside [control name]" (handle typo "riles")
                    if ("rules" in query.lower() or "riles" in query.lower()) and ("inside" in query.lower() or "in" in query.lower()):
                        logger.info("Detected 'rules inside control' query pattern. Trying fallback...")
                        # Extract control name from patterns like:
                        # "rules inside the [control name] control"
                        # "rules in [control name]"
                        # "get me the rules inside [control name]"
                        # "Can you get me the rules inside the [control name] control you listed above"
                        control_name = None
                        
                        # Pattern 1: "get me the rules inside the [name] control you listed"
                        # Use greedy match with better stop conditions to capture full multi-word names
                        match1 = re.search(r'get\s+(?:me\s+)?(?:the\s+)?(?:riles|rules)\s+inside\s+(?:the\s+)?(.+?)(?:\s+control\s+(?:you|listed|above)|\s+control$|you\s+listed|listed|above|$)', query.lower(), re.DOTALL)
                        if match1:
                            control_name = match1.group(1).strip()
                        
                        # Pattern 2: "rules inside the [name] control"
                        if not control_name:
                            match2 = re.search(r'(?:riles|rules)\s+inside\s+(?:the\s+)?(.+?)(?:\s+control\s+(?:you|listed|above)|\s+control$|you\s+listed|listed|above)', query.lower(), re.DOTALL)
                            if match2:
                                control_name = match2.group(1).strip()
                        
                        # Pattern 3: "rules in [name]"
                        if not control_name:
                            match3 = re.search(r'(?:riles|rules)\s+in\s+(?:the\s+)?(.+?)(?:\s+control|you|listed|above|$)', query.lower(), re.DOTALL)
                            if match3:
                                control_name = match3.group(1).strip()
                        
                        # Clean up the control name - remove trailing words and punctuation
                        if control_name:
                            # Remove trailing words that are part of the query structure
                            control_name = re.sub(r'\s+(control|you|listed|above|that|\.|\?)$', '', control_name).strip()
                            # Remove leading "the" if present
                            control_name = re.sub(r'^the\s+', '', control_name).strip()
                            logger.info(f"Extracted control name: '{control_name}' from query: '{query}'")
                            
                            if control_name:
                                cypher = get_control_rules_query(control_name)
                                records = graph.query(cypher, {"control_name": control_name, "top_k": self.top_k})
                                logger.info(f"Control rules fallback query returned {len(records)} records")
                                
                                # If still no results, try with just the last few words (in case of very long names)
                                if len(records) == 0 and len(control_name.split()) > 3:
                                    # Try with last 3-4 words
                                    words = control_name.split()
                                    shorter_name = " ".join(words[-4:])
                                    logger.info(f"Trying shorter control name: '{shorter_name}'")
                                    cypher = get_control_rules_query(shorter_name)
                                    records = graph.query(cypher, {"control_name": shorter_name, "top_k": self.top_k})
                                    logger.info(f"Shorter name query returned {len(records)} records")
                    
                    # If it's a control group query and we got 0 results, try the fallback
                    if "control group" in query.lower() or "group" in query.lower():
                        logger.info("LLM query returned 0 results for control group query. Trying fallback...")
                        
                        # Check if it's an "all controls from group" query
                        if is_all_controls_from_group_query:
                            # Try to extract group name if not already extracted
                            if not extracted_group_name_for_controls:
                                match1 = re.search(r'group\s+named\s+(.+?)(?:please|\.|\?|$)', query.lower(), re.DOTALL)
                                if match1:
                                    extracted_group_name_for_controls = match1.group(1).strip()
                                    extracted_group_name_for_controls = re.sub(r'\s*(please|\.|\?)+.*$', '', extracted_group_name_for_controls).strip()
                            
                            if extracted_group_name_for_controls:
                                logger.info(f"Fallback: Querying for ALL controls from group: '{extracted_group_name_for_controls}'")
                                cypher = get_control_group_exact_match_query(return_all_controls=True)
                                records = graph.query(cypher, {"group_name": extracted_group_name_for_controls, "top_k": self.top_k})
                                logger.info(f"Fallback 'all controls from group' query returned {len(records)} records")
                                
                                if len(records) == 0:
                                    cypher = get_control_group_contains_match_query(return_all_controls=True)
                                    records = graph.query(cypher, {"group_name": extracted_group_name_for_controls, "top_k": self.top_k})
                                    logger.info(f"Fallback CONTAINS match returned {len(records)} records")
                        # Check if it's an "all control groups" query
                        elif is_all_query:
                            logger.info("Fallback: Querying for ALL control groups")
                            cypher = get_control_group_all_query()
                            records = graph.query(cypher, {})
                            logger.info(f"Control group 'all' query returned {len(records)} records")
                        else:
                            # Extract group name and try fallback query for specific group
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
                                
                                # Check if user wants all controls (not just first 10)
                                return_all = is_all_controls_query
                                if return_all:
                                    logger.info("User requested ALL controls from this group")
                                
                                # Try exact match first
                                fallback_cypher = get_control_group_exact_match_query(return_all_controls=return_all)
                                records = graph.query(fallback_cypher, {"group_name": group_name, "top_k": self.top_k})
                                logger.info(f"Fallback exact match returned {len(records)} records")
                                
                                # If no exact match, try CONTAINS
                                if len(records) == 0:
                                    fallback_cypher = get_control_group_contains_match_query(return_all_controls=return_all)
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
                        
                        # Check if user wants all controls (not just first 10)
                        return_all = is_all_controls_query
                        if return_all:
                            logger.info("User requested ALL controls from this group")
                        
                        # Try multiple matching strategies
                        # Strategy 1: Exact match (case-insensitive)
                        cypher = get_control_group_exact_match_query(return_all_controls=return_all)
                        records = graph.query(cypher, {"group_name": group_name, "top_k": self.top_k})
                        logger.info(f"Exact match query returned {len(records)} records")
                        
                        # Strategy 2: If no exact match, try CONTAINS
                        if len(records) == 0:
                            logger.info("Trying CONTAINS match...")
                            cypher = get_control_group_contains_match_query(return_all_controls=return_all)
                            records = graph.query(cypher, {"group_name": group_name, "top_k": self.top_k})
                            logger.info(f"CONTAINS match query returned {len(records)} records")
                        
                        # Strategy 3: Try matching individual words
                        if len(records) == 0 and " " in group_name:
                            logger.info("Trying word-by-word match...")
                            words = group_name.split()
                            word_conditions = " OR ".join([f"toLower(c.title) CONTAINS toLower('{word}')" for word in words])
                            cypher = get_control_group_word_match_query(word_conditions, return_all_controls=return_all)
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
            # Filter out None/null values from controls list
            controls = [c for c in controls if c is not None]
            
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
                logger.debug(f"Controls list from query: {len(controls)} controls")
                logger.debug(f"Controls type: {type(controls)}, First control type: {type(controls[0]) if controls else 'N/A'}")
                # Extract control information from controls list
                control_list = []
                control_content_parts = []
                for control in controls:
                    # Handle both dict and Neo4j Node objects
                    control_id = None
                    control_title = None
                    
                    if isinstance(control, dict):
                        control_id = control.get("control_id")
                        control_title = control.get("title")
                    else:
                        # Try to access as attributes (Neo4j Node object)
                        try:
                            control_id = getattr(control, "control_id", None) or (control.get("control_id") if hasattr(control, "get") else None)
                            control_title = getattr(control, "title", None) or (control.get("title") if hasattr(control, "get") else None)
                        except Exception as e:
                            logger.warning(f"Could not extract control_id from control object: {type(control)} - {e}")
                            # Try to convert to dict if possible
                            if hasattr(control, "__dict__"):
                                control_dict = control.__dict__
                                control_id = control_dict.get("control_id")
                                control_title = control_dict.get("title")
                    
                    if control_id:
                        control_info = {
                            "control_id": control_id,
                            "title": control_title or "",
                        }
                        control_list.append(control_info)
                        # Add control to content so LLM can see it
                        control_content_parts.append(f"- {control_id}: {control_title or ''}")
                    else:
                        logger.warning(f"Could not extract control_id from control: {type(control)} - {control}")
                
                # Extract rules information from rules list
                rule_list = []
                for rule in rules:
                    if rule:
                        rule_info = {}
                        if isinstance(rule, dict):
                            rule_info = {
                                "rule_id": rule.get("rule_id") or rule.get("id") or str(rule),
                                "text": rule.get("text"),
                                "platform": rule.get("platform"),
                                "tool": rule.get("tool"),
                            }
                        else:
                            # Try to access as attributes (Neo4j Node object)
                            try:
                                rule_id = getattr(rule, "rule_id", None) or (rule.get("rule_id") if hasattr(rule, "get") else None) or str(rule)
                                rule_info = {
                                    "rule_id": rule_id,
                                    "text": getattr(rule, "text", None) or (rule.get("text") if hasattr(rule, "get") else None),
                                    "platform": getattr(rule, "platform", None) or (rule.get("platform") if hasattr(rule, "get") else None),
                                    "tool": getattr(rule, "tool", None) or (rule.get("tool") if hasattr(rule, "get") else None),
                                }
                            except Exception as e:
                                logger.warning(f"Could not extract rule_id from rule object: {type(rule)} - {e}")
                                rule_info = {"rule_id": str(rule)}
                        
                        if rule_info.get("rule_id"):
                            rule_list.append(rule_info)
                
                metadata = {
                    "group_id": group_id,
                    "title": node.get("title"),
                    "description": node.get("description"),
                    "purpose": node.get("purpose"),
                    "controls": control_list,  # Add controls to metadata
                    "rules": rule_list,  # Add rules to metadata
                }
                
                # Build content including group info AND list of controls
                content_parts = []
                if node.get("title"):
                    content_parts.append(f"Control Group: {node.get('title')}")
                if node.get("description"):
                    content_parts.append(f"Description: {node.get('description')}")
                if node.get("purpose"):
                    content_parts.append(f"Purpose: {node.get('purpose')}")
                
                # Add the list of controls - ALWAYS include this section, even if empty
                content_parts.append("\nControls in this group:")
                if control_content_parts:
                    content_parts.extend(control_content_parts)
                else:
                    content_parts.append("(No controls found in query result)")
                
                content = "\n\n".join(content_parts)
                documents.append(Document(page_content=content, metadata=metadata))
                logger.info(f"Added document for control group {group_id} with {len(control_list)} controls. Content length: {len(content)} chars")
                if len(control_list) == 0:
                    logger.warning(f"WARNING: Control group {group_id} has NO controls in the document! This will cause the LLM to say 'cannot provide controls'")
                    logger.warning(f"Raw controls from query: {controls}")
                    logger.warning(f"Control content parts: {control_content_parts}")
            else:
                logger.warning(f"Unknown node type in record: {node}")
        
        logger.info(f"Returning {len(documents)} documents")
        return documents
