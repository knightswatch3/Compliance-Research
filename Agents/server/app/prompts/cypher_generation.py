"""Prompt templates for Cypher query generation."""

CYPHER_GENERATION_PROMPT = """You are a Cypher query expert for a Neo4j graph database.

Database Schema:
{schema}

User Question: "{user_query}"

CRITICAL REQUIREMENTS - YOU MUST FOLLOW THESE EXACTLY:
1. Return ONLY the Cypher query, no explanation or markdown
2. Use proper Cypher syntax
3. ALWAYS use alias "c" for the main node you're returning (whether it's Control or ControlGroup)
4. ALWAYS include: OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule) to get rules
5. ALWAYS return in this EXACT format: RETURN c, collect(r) AS rules
6. Use LIMIT $top_k at the end UNLESS the user asks for "all", "every", "list all", "show all", "get all", or "count" - in those cases, DO NOT include LIMIT
7. Include parameters $query and $top_k where appropriate (but only if using LIMIT)

QUERY PATTERNS:

1. For individual controls (by ID or name):
   MATCH (c:Control)
   WHERE toLower(c.control_id) CONTAINS toLower($query)
      OR toLower(c.title) CONTAINS toLower($query)
      OR toLower(c.description) CONTAINS toLower($query)
   OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
   RETURN c, collect(r) AS rules
   LIMIT $top_k

1a. For queries asking about "rules inside [control name]" or "rules in [control name]":
   Extract the control name from the query and use:
   MATCH (c:Control)
   WHERE toLower(c.title) CONTAINS toLower($query)
      OR toLower(c.control_id) CONTAINS toLower($query)
   OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
   RETURN c, collect(r) AS rules
   LIMIT $top_k

2. For control groups (by name/title or ID):
   MATCH (c:ControlGroup)
   WHERE toLower(c.title) CONTAINS toLower($query)
      OR toLower(c.id) CONTAINS toLower($query)
      OR toLower(c.description) CONTAINS toLower($query)
   OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)-[:HAS_RULE]->(r:Rule)
   RETURN c, collect(r) AS rules
   LIMIT $top_k

3. For listing all control groups (when user asks for "all"):
   MATCH (c:ControlGroup)
   OPTIONAL MATCH (c)<-[:IN_GROUP]-(control:Control)-[:HAS_RULE]->(r:Rule)
   RETURN c, collect(r) AS rules
   (NO LIMIT - return all results)

4. For dependencies:
   MATCH (target:Control {{control_id: $control_id}})
   OPTIONAL MATCH (c:Control)-[:RELATES_TO]->(target)
   OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
   RETURN c, collect(r) AS rules
   LIMIT $top_k

IMPORTANT:
- When querying ControlGroup, you can get rules through: (c)<-[:IN_GROUP]-(control:Control)-[:HAS_RULE]->(r:Rule)
- When querying Control, use: (c)-[:HAS_RULE]->(r:Rule)
- The main node MUST be aliased as "c" and rules MUST be aliased as "rules" in the RETURN clause.
- For "access control" or similar group names, query ControlGroup with WHERE toLower(c.title) CONTAINS toLower($query)

Cypher Query:"""


def get_cypher_generation_prompt(schema: str, user_query: str) -> str:
    """Format the Cypher generation prompt with schema and user query.
    
    Args:
        schema: The Neo4j database schema as a string
        user_query: The user's natural language question
        
    Returns:
        Formatted prompt string ready to send to LLM
    """
    return CYPHER_GENERATION_PROMPT.format(
        schema=schema,
        user_query=user_query
    )

