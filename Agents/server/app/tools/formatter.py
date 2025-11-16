"""Formatter tool for formatting document responses using LLM."""

import logging
from typing import List, Optional, Any, Dict

from langchain.schema import Document
from langchain_google_genai import ChatGoogleGenerativeAI

logger = logging.getLogger(__name__)


class DocumentFormatter:
    """Tool to format retrieved documents into a structured response using LLM."""
    
    def __init__(self, llm: Optional[ChatGoogleGenerativeAI] = None):
        """Initialize the formatter with an LLM.
        
        Args:
            llm: The LLM instance to use for formatting. If None, will create one.
        """
        self.llm = llm
        if self.llm is None:
            from dotenv import load_dotenv
            import os
            load_dotenv()
            self.llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0.0,
                timeout=30,
            )
    
    def _clean_metadata_value(self, value):
        """Convert metadata values to simple, serializable types."""
        # Handle None
        if value is None:
            return None
        
        # Handle dict - recursively clean
        if isinstance(value, dict):
            return {k: self._clean_metadata_value(v) for k, v in value.items()}
        
        # Handle list/tuple - recursively clean each item
        if isinstance(value, (list, tuple)):
            return [self._clean_metadata_value(item) for item in value]
        
        # Handle objects with __dict__ (like Neo4j Node objects)
        if hasattr(value, '__dict__') and not isinstance(value, (str, int, float, bool)):
            # Try to convert to dict
            try:
                return {k: self._clean_metadata_value(v) for k, v in value.__dict__.items() if not k.startswith('_')}
            except:
                return str(value)
        
        # Handle objects with items() method (dict-like)
        if hasattr(value, 'items') and callable(getattr(value, 'items')):
            try:
                return {k: self._clean_metadata_value(v) for k, v in value.items()}
            except:
                return str(value)
        
        # Primitive types - return as-is
        if isinstance(value, (str, int, float, bool)):
            return value
        
        # Fallback: convert to string
        return str(value)
    
    def _clean_metadata(self, metadata):
        """Clean metadata dictionary to contain only simple, serializable types."""
        if not metadata:
            return {}
        
        cleaned = {}
        for key, value in metadata.items():
            try:
                cleaned[key] = self._clean_metadata_value(value)
            except Exception as e:
                logger.warning(f"Could not clean metadata key '{key}': {e}")
                cleaned[key] = str(value)
        
        return cleaned
    
    def format_documents(
        self,
        documents: List[Document],
        user_query: str,
        context: str = None
    ) -> Dict[str, Any]:
        """Format retrieved documents into a structured response.
        
        Args:
            documents: List of retrieved documents (array of Document objects)
            user_query: The user's query string
            context: Optional additional context (e.g., conversation history)
            
        Returns:
            Dictionary with:
            - answer: The formatted answer text
            - control_groups: List of control group objects
            - controls: List of control IDs
            - rules: List of rule IDs
        """
        if not documents:
            return {
                "answer": "I don't have enough information to answer this question.",
                "control_groups": [],
                "controls": [],
                "rules": []
            }
        
        # Detect if this is a query about controls in a specific group
        is_controls_in_group_query = any(phrase in user_query.lower() for phrase in [
            "controls in", "controls from", "controls under", "controls in the group",
            "controls from the group", "list controls", "all controls"
        ]) and any(phrase in user_query.lower() for phrase in [
            "group", "control group"
        ])
        
        # Extract group name from query if it's a controls-in-group query
        requested_group_name = None
        if is_controls_in_group_query:
            # Try to extract group name from query patterns like "group named X", "group X", etc.
            import re
            patterns = [
                r"group\s+named\s+(.+?)(?:\?|$)",
                r"group\s+called\s+(.+?)(?:\?|$)",
                r"control\s+group\s+named\s+(.+?)(?:\?|$)",
                r"control\s+group\s+(.+?)(?:\?|$)",
                r"in\s+the\s+control\s+group\s+(.+?)(?:\?|$)",
                r"in\s+the\s+group\s+(.+?)(?:\?|$)",
                r"from\s+the\s+control\s+group\s+(.+?)(?:\?|$)",
                r"from\s+the\s+group\s+(.+?)(?:\?|$)",
            ]
            for pattern in patterns:
                match = re.search(pattern, user_query, re.IGNORECASE)
                if match:
                    requested_group_name = match.group(1).strip()
                    # Clean up common trailing words
                    requested_group_name = re.sub(r'\s+(control\s+group|group|named|called|\.|\?)$', '', requested_group_name, flags=re.IGNORECASE).strip()
                    break
            
            logger.info(f"Extracted requested group name: '{requested_group_name}'")
        
        # Extract all controls, rules, and control groups from all documents
        all_controls = []  # List of dicts with control_id, title, group_id
        all_rules = []
        all_control_groups = []
        specific_group = None  # For single group queries
        
        for doc in documents:
            if doc.metadata:
                cleaned_metadata = self._clean_metadata(doc.metadata)
                
                # Check if this is a Control document (not a ControlGroup)
                if cleaned_metadata.get("control_id"):
                    # This is a Control node - extract it as a control
                    control_id = cleaned_metadata.get("control_id")
                    control_title = cleaned_metadata.get("title")
                    control_group_id = cleaned_metadata.get("group_id")
                    
                    if control_id:
                        existing_control = next((c for c in all_controls if isinstance(c, dict) and c.get("control_id") == control_id), None)
                        if not existing_control:
                            control_obj = {
                                "control_id": control_id,
                                "title": control_title,
                                "group_id": control_group_id
                            }
                            all_controls.append(control_obj)
                            logger.debug(f"Added control from Control node: {control_id} (title: {control_title}, group_id: {control_group_id})")
                
                # Check if this is a ControlGroup document
                elif cleaned_metadata.get("group_id") or cleaned_metadata.get("id"):
                    # This is a control group
                    group_id = cleaned_metadata.get("group_id") or cleaned_metadata.get("id")
                    group_title = cleaned_metadata.get("title") or cleaned_metadata.get("group_title")
                    
                    if group_id:
                        group_info = {
                            "id": group_id,
                            "title": group_title or "",
                            "description": cleaned_metadata.get("description") or cleaned_metadata.get("group_description") or ""
                        }
                        
                        # For controls-in-group queries, only match the requested group
                        if is_controls_in_group_query and requested_group_name:
                            # Check if this group matches the requested name
                            group_title_lower = (group_title or "").lower()
                            requested_name_lower = requested_group_name.lower()
                            
                            # Match if title contains requested name or vice versa
                            if (requested_name_lower in group_title_lower or 
                                group_title_lower in requested_name_lower or
                                group_title_lower == requested_name_lower):
                                if not specific_group:
                                    specific_group = group_info
                                    logger.info(f"Matched requested group: {group_title} (ID: {group_id})")
                                    logger.info(f"Group metadata keys: {list(cleaned_metadata.keys())}")
                                    
                                    # Extract controls from THIS group's metadata
                                    controls_in_metadata = cleaned_metadata.get("controls")
                                    logger.info(f"Controls in metadata: {controls_in_metadata}")
                                    logger.info(f"Controls type: {type(controls_in_metadata)}, length: {len(controls_in_metadata) if controls_in_metadata else 0}")
                                    
                                    if controls_in_metadata and len(controls_in_metadata) > 0:
                                        logger.info(f"Found {len(controls_in_metadata)} controls in metadata")
                                        for ctrl in controls_in_metadata:
                                            logger.debug(f"Processing control: {type(ctrl)} - {ctrl}")
                                            if isinstance(ctrl, dict):
                                                ctrl_id = ctrl.get("control_id") or ctrl.get("id")
                                                ctrl_title = ctrl.get("title")
                                                if ctrl_id:
                                                    # Check if we already have this control (by ID)
                                                    existing_control = next((c for c in all_controls if isinstance(c, dict) and c.get("control_id") == ctrl_id), None)
                                                    if not existing_control:
                                                        control_obj = {
                                                            "control_id": ctrl_id,
                                                            "title": ctrl_title,
                                                            "group_id": group_id  # Use the matched group's ID
                                                        }
                                                        all_controls.append(control_obj)
                                                        logger.info(f"Added control: {ctrl_id} (title: {ctrl_title}, group_id: {group_id})")
                                                    else:
                                                        # Update existing control if it doesn't have title/group_id
                                                        if not existing_control.get("title") and ctrl_title:
                                                            existing_control["title"] = ctrl_title
                                                        if not existing_control.get("group_id") and group_id:
                                                            existing_control["group_id"] = group_id
                                                else:
                                                    logger.warning(f"Control dict missing control_id. Keys: {list(ctrl.keys())}")
                                            else:
                                                logger.warning(f"Control is not a dict: {type(ctrl)} - {ctrl}")
                                    else:
                                        logger.warning(f"No controls found in metadata for group {group_title}. Metadata keys: {list(cleaned_metadata.keys())}")
                                        # Also check raw metadata before cleaning
                                        if doc.metadata and "controls" in doc.metadata:
                                            raw_controls = doc.metadata.get("controls")
                                            logger.warning(f"Raw metadata has 'controls' key: {type(raw_controls)}, value: {raw_controls}")
                                            # Try to extract from raw metadata
                                            if raw_controls:
                                                for ctrl in raw_controls:
                                                    if isinstance(ctrl, dict):
                                                        ctrl_id = ctrl.get("control_id") or ctrl.get("id")
                                                        ctrl_title = ctrl.get("title")
                                                        if ctrl_id:
                                                            existing_control = next((c for c in all_controls if isinstance(c, dict) and c.get("control_id") == ctrl_id), None)
                                                            if not existing_control:
                                                                control_obj = {
                                                                    "control_id": ctrl_id,
                                                                    "title": ctrl_title,
                                                                    "group_id": group_id
                                                                }
                                                                all_controls.append(control_obj)
                                                                logger.info(f"Added control from raw metadata: {ctrl_id} (title: {ctrl_title}, group_id: {group_id})")
                                        
                                        # Fallback: Try to extract control IDs from page_content
                                        if doc.page_content and len(all_controls) == 0:
                                            import re
                                            # Look for patterns like "AT-1", "AT-2", etc.
                                            control_pattern = r'\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b'
                                            found_controls = re.findall(control_pattern, doc.page_content)
                                            if found_controls:
                                                logger.info(f"Extracted {len(found_controls)} controls from page_content: {found_controls}")
                                                for ctrl_id in found_controls:
                                                    existing_control = next((c for c in all_controls if isinstance(c, dict) and c.get("control_id") == ctrl_id), None)
                                                    if not existing_control:
                                                        control_obj = {
                                                            "control_id": ctrl_id,
                                                            "title": None,  # Can't extract title from regex
                                                            "group_id": group_id
                                                        }
                                                        all_controls.append(control_obj)
                                                        logger.info(f"Added control from page_content: {ctrl_id} (group_id: {group_id})")
                        else:
                            # Not a controls-in-group query, or no specific name requested
                            # Avoid duplicates
                            if not any(g.get("id") == group_id for g in all_control_groups):
                                all_control_groups.append(group_info)
                
                # Extract controls from metadata (only if not already extracted for specific group)
                # This handles cases where controls are in documents that aren't the matched group
                if not (is_controls_in_group_query and specific_group):
                    if cleaned_metadata.get("controls"):
                        doc_group_id = cleaned_metadata.get("group_id") or cleaned_metadata.get("id")
                        for ctrl in cleaned_metadata.get("controls", []):
                            if isinstance(ctrl, dict):
                                ctrl_id = ctrl.get("control_id") or ctrl.get("id")
                                ctrl_title = ctrl.get("title")
                                if ctrl_id:
                                    existing_control = next((c for c in all_controls if isinstance(c, dict) and c.get("control_id") == ctrl_id), None)
                                    if not existing_control:
                                        control_obj = {
                                            "control_id": ctrl_id,
                                            "title": ctrl_title,
                                            "group_id": doc_group_id
                                        }
                                        all_controls.append(control_obj)
                
                # Extract rules (full objects with rule_id, platform, tool)
                if cleaned_metadata.get("rules"):
                    for rule in cleaned_metadata.get("rules", []):
                        if isinstance(rule, dict):
                            rule_id = rule.get("rule_id") or rule.get("id")
                            if rule_id:
                                # Check if we already have this rule (by ID)
                                existing_rule = next((r for r in all_rules if isinstance(r, dict) and r.get("rule_id") == rule_id), None)
                                if not existing_rule:
                                    rule_obj = {
                                        "rule_id": rule_id,
                                        "platform": rule.get("platform"),
                                        "tool": rule.get("tool"),
                                    }
                                    all_rules.append(rule_obj)
                                    logger.debug(f"Added rule: {rule_id} (platform: {rule.get('platform')}, tool: {rule.get('tool')})")
                                else:
                                    # Update existing rule if it doesn't have platform/tool
                                    if not existing_rule.get("platform") and rule.get("platform"):
                                        existing_rule["platform"] = rule.get("platform")
                                    if not existing_rule.get("tool") and rule.get("tool"):
                                        existing_rule["tool"] = rule.get("tool")
                        else:
                            # If it's not a dict, try to convert to rule_id string
                            rule_id = str(rule) if rule else None
                            if rule_id:
                                existing_rule = next((r for r in all_rules if isinstance(r, dict) and r.get("rule_id") == rule_id), None)
                                if not existing_rule:
                                    all_rules.append({"rule_id": rule_id, "platform": None, "tool": None})
        
        # Create formatted answer based on what was found
        if is_controls_in_group_query and specific_group:
            # Query about controls in a specific group
            group_name = specific_group.get("title") or specific_group.get("id", "Unknown")
            group_desc = specific_group.get("description", "")
            controls_count = len(all_controls)
            
            answer_parts = [f"The '{group_name}' control group"]
            if group_desc:
                answer_parts.append(f"({group_desc[:100]}{'...' if len(group_desc) > 100 else ''})")
            answer_parts.append(f"contains {controls_count} control(s).")
            
            if controls_count > 0:
                answer_parts.append(f"Controls: {', '.join(all_controls[:20])}")
                if controls_count > 20:
                    answer_parts.append(f"and {controls_count - 20} more.")
            
            formatted_answer = " ".join(answer_parts)
            # Only include the specific group in control_groups array
            all_control_groups = [specific_group]
        elif all_control_groups and not is_controls_in_group_query:
            # Summary for control groups (list all groups query)
            group_names = [g.get("title") or g.get("id", "Unknown") for g in all_control_groups]
            formatted_answer = f"Found {len(all_control_groups)} control group(s): {', '.join(group_names)}"
        elif all_controls:
            # Summary for controls - extract just IDs for display
            control_ids = [c.get("control_id") if isinstance(c, dict) else c for c in all_controls]
            formatted_answer = f"Found {len(all_controls)} control(s): {', '.join(control_ids[:10])}"
            if len(all_controls) > 10:
                formatted_answer += f" and {len(all_controls) - 10} more"
        elif all_rules:
            # Summary for rules
            formatted_answer = f"Found {len(all_rules)} rule(s)"
        else:
            # Fallback: use document content
            formatted_content = []
            for doc in documents:
                if doc.page_content:
                    # Take first 200 chars for summary
                    content = doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content
                    formatted_content.append(content)
            formatted_answer = "\n\n".join(formatted_content) if formatted_content else "No content found in documents."
        
        return {
            "answer": formatted_answer,
            "control_groups": all_control_groups,
            "controls": all_controls,
            "rules": all_rules
        }

