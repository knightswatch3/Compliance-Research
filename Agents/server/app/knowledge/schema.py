from langchain_neo4j import Neo4jGraph


def get_fallback_schema() -> str:
    """Get fallback Neo4j schema when dynamic schema fetching fails.
    
    Returns a hardcoded schema string that represents the expected structure
    of the Neo4j database for compliance controls.
    """
    return """Neo4j Graph Database Schema:

NODES:
- Control: {control_id, title, description, tags}
- ControlGroup: {id, title, description, purpose, tags}
- Rule: {rule_id, text, type, platform, tool, tags}
- Framework: {name, revision}

RELATIONSHIPS:
- (Control)-[:IN_GROUP]->(ControlGroup)
- (Control)-[:HAS_RULE]->(Rule)
- (Control)-[:RELATES_TO]->(Control)
- (ControlGroup)-[:BELONGS_TO]->(Framework)
- (Rule)-[:DEPENDS_ON_RULE]->(Rule)
- (Rule)-[:DEPENDS_ON_CONTROL]->(Control)
"""


def get_neo4j_schema(graph: Neo4jGraph) -> str:
    """Get Neo4j schema and format it as a string for LLM context."""
    
    # Get node properties
    node_props_query = """
    CALL db.schema.nodeTypeProperties()
    YIELD nodeType, propertyName, propertyTypes
    RETURN nodeType, collect({name: propertyName, types: propertyTypes}) AS properties
    """
    
    # Get relationship types
    rel_query = """
    CALL db.schema.relTypeProperties()
    YIELD relType, propertyName, propertyTypes
    RETURN relType, collect({name: propertyName, types: propertyTypes}) AS properties
    """
    
    try:
        node_results = graph.query(node_props_query)
        rel_results = graph.query(rel_query)
        
        schema_text = "Neo4j Graph Database Schema:\n\n"
        schema_text += "NODES:\n"
        for record in node_results:
            node_type = record.get("nodeType", "")
            props = record.get("properties", [])
            prop_list = ", ".join([p["name"] for p in props])
            schema_text += f"- {node_type}: {{{prop_list}}}\n"
        
        schema_text += "\nRELATIONSHIPS:\n"
        for record in rel_results:
            rel_type = record.get("relType", "")
            props = record.get("properties", [])
            prop_list = ", ".join([p["name"] for p in props]) if props else "no properties"
            schema_text += f"- {rel_type}: {{{prop_list}}}\n"
        
        # Add common relationship patterns
        schema_text += "\nCOMMON PATTERNS:\n"
        schema_text += "- (Control)-[:IN_GROUP]->(ControlGroup)\n"
        schema_text += "- (Control)-[:HAS_RULE]->(Rule)\n"
        schema_text += "- (Control)-[:RELATES_TO]->(Control)\n"
        schema_text += "- (ControlGroup)-[:BELONGS_TO]->(Framework)\n"
        schema_text += "- (Rule)-[:DEPENDS_ON_RULE]->(Rule)\n"
        schema_text += "- (Rule)-[:DEPENDS_ON_CONTROL]->(Control)\n"
        
        return schema_text
    except Exception as e:
        # Fallback to hardcoded schema if query fails
        return get_fallback_schema()