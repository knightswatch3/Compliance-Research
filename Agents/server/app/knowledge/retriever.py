import os

from dotenv import load_dotenv
from langchain.schema import BaseRetriever, Document
from langchain_neo4j import Neo4jGraph

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
    raise RuntimeError("Neo4j credentials are not fully configured in the environment")


graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD)


class Neo4jControlRetriever(BaseRetriever):
    """Simple keyword-based retriever for controls stored in Neo4j."""

    top_k: int = 5

    def _get_relevant_documents(self, query: str) -> list[Document]:
        cypher = """
        MATCH (c:Control)
        WHERE toLower(c.title) CONTAINS toLower($query)
           OR toLower(c.control_id) CONTAINS toLower($query)
           OR toLower(c.description) CONTAINS toLower($query)
        OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule)
        RETURN c, collect(r) AS rules
        LIMIT $top_k
        """
        records = graph.query(cypher, {"query": query, "top_k": self.top_k})
        documents: list[Document] = []
        for record in records:
            control = record["c"]
            rules = record.get("rules", [])
            metadata = {
                "control_id": control.get("control_id"),
                "title": control.get("title"),
                "group_id": control.get("control_group"),
                "rules": [
                    {
                        "rule_id": rule.get("rule_id"),
                        "text": rule.get("text"),
                        "platform": rule.get("platform"),
                    }
                    for rule in rules
                    if rule
                ],
            }
            content = "\n\n".join(filter(None, [control.get("title"), control.get("description")]))
            documents.append(Document(page_content=content, metadata=metadata))
        return documents
