# RAG Agent Behavior Guide

## What is RAG (Retrieval-Augmented Generation)?

RAG combines **information retrieval** with **language generation** to answer questions using your knowledge base (Neo4j in this case) rather than just the LLM's training data.

## How Your RAG Agent Works

### 1. **Query Processing Flow**

```
User Question
    ↓
Retriever (Neo4jControlRetriever)
    ↓
Searches Neo4j for relevant Controls/Rules
    ↓
Returns top_k most relevant documents
    ↓
LLM (Gemini) receives:
    - User question
    - Retrieved context (controls/rules from Neo4j)
    ↓
LLM generates answer based on retrieved context
    ↓
Response returned to user
```

### 2. **Expected Behavior**

#### ✅ **Good RAG Behavior:**
- **Answers are grounded in your data**: The LLM should cite specific controls, rules, or IDs from Neo4j
- **Retrieves relevant documents**: When you ask about "access control", it should find AC-* controls
- **Combines multiple sources**: Can synthesize information from multiple controls/rules
- **Handles "I don't know"**: If nothing relevant is found, should say so rather than hallucinate

#### ❌ **Bad RAG Behavior (Red Flags):**
- **Hallucination**: Making up control IDs or requirements not in your database
- **Ignoring context**: Answering from training data instead of retrieved documents
- **Empty retrievals**: No documents found when relevant data exists
- **Irrelevant retrievals**: Finding wrong controls (e.g., finding AC-* when asking about audit)

### 3. **What Should Be Returned**

Your `ChatResponse` should include:

```json
{
  "answer": "The answer text...",
  "controls": [
    {
      "control_id": "AC-1",
      "title": "Access Control Policy and Procedures",
      "group_id": "AC"
    }
  ],
  "rules": [
    {
      "rule_id": "rule-123",
      "platform": "AWS",
      "tool": "CloudFormation"
    }
  ],
  "citations": [
    {
      "label": "AC-1",
      "snippet": "Relevant text excerpt..."
    }
  ]
}
```

### 4. **Testing Your RAG Agent**

#### Test Case 1: Specific Control Query
**Question**: "What is AC-1?"
**Expected**:
- ✅ Retrieves AC-1 control from Neo4j
- ✅ Returns AC-1 in `controls` array
- ✅ Answer mentions AC-1 specifically
- ✅ Includes rules if AC-1 has any

#### Test Case 2: Topic-Based Query
**Question**: "What are the authentication requirements?"
**Expected**:
- ✅ Retrieves multiple IA-* (Identification & Authentication) controls
- ✅ Returns multiple controls in `controls` array
- ✅ Answer synthesizes information from multiple controls
- ✅ Mentions specific control IDs

#### Test Case 3: No Match Query
**Question**: "What is PCI DSS Requirement 10?"
**Expected**:
- ⚠️ No controls retrieved (PCI not in database yet)
- ⚠️ Empty `controls` array
- ✅ LLM should indicate it doesn't have that information, or answer from general knowledge (but note it's not from your database)

### 5. **Current Implementation Status**

**What's Working:**
- ✅ Retriever connects to Neo4j
- ✅ Searches for controls by title, ID, or description
- ✅ Returns top_k documents
- ✅ LLM generates answers

**What Needs Fixing:**
- ❌ Controls/rules not extracted from retrieved documents
- ❌ Citations not populated
- ❌ No visibility into what documents were retrieved
- ❌ No logging/debugging of retrieval process

### 6. **Debugging Your RAG Agent**

To see what's happening:

1. **Check what documents are retrieved**: Log the documents returned by the retriever
2. **Check what context is sent to LLM**: See what text is passed to Gemini
3. **Check LLM response**: Verify it's using the retrieved context
4. **Check Neo4j queries**: Verify the Cypher query is finding the right controls

### 7. **Improving RAG Performance**

**Better Retrieval:**
- Use semantic search (vector embeddings) instead of keyword search
- Improve Cypher queries to find related controls
- Add filters (e.g., by framework, by control group)

**Better Context:**
- Include more metadata (control group, related controls)
- Add rule information to context
- Include examples or use cases

**Better Prompting:**
- Instruct LLM to cite sources
- Ask LLM to extract control IDs from context
- Request structured output (JSON with controls/rules)

## Next Steps

1. Add logging to see retrieved documents
2. Extract controls/rules from document metadata
3. Populate citations array
4. Test with NIST control questions
5. Consider adding semantic search for better retrieval

