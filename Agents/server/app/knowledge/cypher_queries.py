"""Cypher query templates for Neo4j control and control group queries."""


def get_control_group_exact_match_query(return_all_controls: bool = False) -> str:
    """Get Cypher query for exact match of control group by name (case-insensitive).
    
    Args:
        return_all_controls: If True, returns all controls. If False, returns first 10.
    
    Returns the control group with controls and all rules.
    """
    controls_limit = "" if return_all_controls else "[0..10]"
    limit_clause = "" if return_all_controls else "LIMIT $top_k"
    return f"""
    MATCH (c:ControlGroup)
    WHERE toLower(c.title) = toLower($group_name)
       OR toLower(c.id) = toLower($group_name)
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)
    WITH c, collect(DISTINCT control){controls_limit} AS all_controls
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(ctrl:Control)-[:HAS_RULE]->(r:Rule)
    WITH c, all_controls, collect(DISTINCT r) AS all_rules
    RETURN c, 
           [ctrl IN all_controls WHERE ctrl IS NOT NULL] AS controls, 
           [rule IN all_rules WHERE rule IS NOT NULL] AS rules
    {limit_clause}
    """


def get_control_group_contains_match_query(return_all_controls: bool = False) -> str:
    """Get Cypher query for CONTAINS match of control group by name.
    
    Args:
        return_all_controls: If True, returns all controls. If False, returns first 10.
    
    Returns the control group with controls and all rules.
    """
    controls_limit = "" if return_all_controls else "[0..10]"
    limit_clause = "" if return_all_controls else "LIMIT $top_k"
    return f"""
    MATCH (c:ControlGroup)
    WHERE toLower(c.title) CONTAINS toLower($group_name)
       OR toLower(c.id) CONTAINS toLower($group_name)
       OR toLower(c.description) CONTAINS toLower($group_name)
    WITH c
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)
    WITH c, 
         CASE WHEN control IS NOT NULL 
         THEN collect(DISTINCT control){controls_limit} 
         ELSE [] 
         END AS controls
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(ctrl:Control)-[:HAS_RULE]->(r:Rule)
    WITH c, controls, 
         CASE WHEN r IS NOT NULL 
         THEN collect(DISTINCT r) 
         ELSE [] 
         END AS rules
    RETURN c, 
           coalesce(controls, []) AS controls, 
           coalesce(rules, []) AS rules
    {limit_clause}
    """


def get_control_group_word_match_query(word_conditions: str, return_all_controls: bool = False) -> str:
    """Get Cypher query for word-by-word match of control group.
    
    Args:
        word_conditions: Pre-formatted WHERE conditions for word matching
        return_all_controls: If True, returns all controls. If False, returns first 10.
        
    Returns the control group with controls and all rules.
    """
    controls_limit = "" if return_all_controls else "[0..10]"
    limit_clause = "" if return_all_controls else "LIMIT $top_k"
    return f"""
    MATCH (c:ControlGroup)
    WHERE {word_conditions}
       OR toLower(c.id) CONTAINS toLower($group_name)
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)
    WITH c, 
         CASE WHEN control IS NOT NULL 
         THEN collect(DISTINCT control){controls_limit} 
         ELSE [] 
         END AS all_controls
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(ctrl:Control)-[:HAS_RULE]->(r:Rule)
    WITH c, all_controls, 
         CASE WHEN r IS NOT NULL 
         THEN collect(DISTINCT r) 
         ELSE [] 
         END AS all_rules
    RETURN c, 
           coalesce(all_controls, []) AS controls, 
           coalesce(all_rules, []) AS rules
    {limit_clause}
    """


def get_control_group_all_query() -> str:
    """Get Cypher query for all control groups (no limit).
    
    Returns all control groups with their rules.
    """
    return """
    MATCH (c:ControlGroup)
    OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    """


def get_control_dependency_query(control_id: str) -> str:
    """Get Cypher query for controls that depend on a given control.
    
    Args:
        control_id: The control ID to find dependencies for
        
    Returns dependent controls with their rules.
    """
    return f"""
    MATCH (target:Control {{control_id: "{control_id}"}})
    OPTIONAL MATCH (dependent1:Control)-[:RELATES_TO]->(target)
    OPTIONAL MATCH (target)-[:RELATES_TO]->(dependent2:Control)
    OPTIONAL MATCH (rule:Rule)-[:DEPENDS_ON_CONTROL]->(target)
    OPTIONAL MATCH (dependent3:Control)-[:HAS_RULE]->(rule)
    WITH collect(DISTINCT dependent1) + collect(DISTINCT dependent2) + collect(DISTINCT dependent3) AS all_dependent
    UNWIND all_dependent AS c
    WHERE c IS NOT NULL
    OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    LIMIT $top_k
    """


def get_control_dependency_fallback_query() -> str:
    """Get Cypher query for controls that depend on a control (fallback with query parameter).
    
    Returns dependent controls with their rules.
    """
    return """
    MATCH (target:Control)
    WHERE toLower(target.control_id) CONTAINS toLower($query)
       OR toLower(target.title) CONTAINS toLower($query)
    OPTIONAL MATCH (dependent1:Control)-[:RELATES_TO]->(target)
    OPTIONAL MATCH (target)-[:RELATES_TO]->(dependent2:Control)
    OPTIONAL MATCH (rule:Rule)-[:DEPENDS_ON_CONTROL]->(target)
    OPTIONAL MATCH (dependent3:Control)-[:HAS_RULE]->(rule)
    WITH collect(DISTINCT dependent1) + collect(DISTINCT dependent2) + collect(DISTINCT dependent3) AS all_dependent
    UNWIND all_dependent AS c
    WHERE c IS NOT NULL
    OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    LIMIT $top_k
    """


def get_control_all_query() -> str:
    """Get Cypher query for all controls (no limit).
    
    Returns all controls with their rules.
    """
    return """
    MATCH (c:Control)
    OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    ORDER BY c.control_id
    """


def get_control_search_query() -> str:
    """Get Cypher query for searching controls by title, ID, or description.
    
    Returns matching controls with their rules.
    """
    return """
    MATCH (c:Control)
    WHERE toLower(c.title) CONTAINS toLower($query)
       OR toLower(c.control_id) CONTAINS toLower($query)
       OR toLower(c.description) CONTAINS toLower($query)
    OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    LIMIT $top_k
    """


def get_control_rules_query(control_name: str) -> str:
    """Get Cypher query for finding rules inside a specific control by name.
    
    Args:
        control_name: The control title or name to search for
        
    Returns the control with all its rules.
    """
    return """
    MATCH (c:Control)
    WHERE toLower(c.title) CONTAINS toLower($control_name)
       OR toLower(c.control_id) CONTAINS toLower($control_name)
    OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
    RETURN c, collect(r) AS rules
    LIMIT $top_k
    """

