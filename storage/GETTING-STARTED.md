# Getting Started with Neo4J Compliance Research System

## Overview
This guide will help you set up Neo4J in a Podman container and understand how to convert your PDF compliance documents into a Neo4J knowledge graph.

---

## Step 1: Start Neo4J Container

### What We're Setting Up:
- **Neo4J Database** running in Podman container
- **Persistent Storage** - All data stored on your Mac (won't be lost when container stops)
- **Web GUI** - Neo4J Browser accessible at http://localhost:7474
- **Ports**: 
  - 7474: Web interface (Browser GUI)
  - 7687: Database connection (Bolt protocol)

### Run the Setup Script:
```bash
./neo4j-podman.sh
```

### What the Script Does:
1. Creates `neo4j-data` folder structure in your project
2. Creates directories for:
   - `data/` - Database files (persistent)
   - `logs/` - Neo4J logs
   - `import/` - CSV/JSON files to import
   - `plugins/` - Neo4J plugins (APOC, etc.)
3. Starts Neo4J container with volume mounts to your Mac
4. Sets up APOC plugin (Advanced Neo4J Procedures)

### Access Neo4J Browser:
1. Open your web browser
2. Go to: http://localhost:7474
3. Login with:
   - Username: `neo4j`
   - Password: `compliance123`

### Verify It's Working:
Once logged into Neo4J Browser, try this query:
```cypher
RETURN "Hello Compliance Research!" as message
```

---

## Step 2: Understanding PDF to Neo4J Conversion Process

### The Challenge:
PDF documents contain unstructured text. We need to extract:
- **Document Structure** (Sections, Subsections)
- **Requirements** (Individual compliance rules)
- **Relationships** (References, Dependencies)
- **Metadata** (Framework name, version, compliance level)

### The Process (High-Level):

#### Phase 1: PDF Parsing
**Goal**: Extract text and structure from PDFs

1. **Read PDF files**:
   - PCI DSS documents from `documents/pci-dss/`
   - FedRAMP documents from `documents/fedramp/`

2. **Extract text**:
   - Convert PDF pages to text
   - Preserve formatting where possible

3. **Identify structure**:
   - Find section headers (e.g., "REQUIREMENT 1:", "Section 164.308")
   - Identify requirement numbers (e.g., "1.1.1", "164.308(a)(1)")
   - Extract requirement text

#### Phase 2: Data Modeling
**Goal**: Structure the extracted data for Neo4J

**Neo4J Nodes (Entities) we'll create:**
- `Framework` - e.g., "PCI DSS", "FedRAMP"
- `Document` - e.g., "PCI-DSS-v4_0_1.pdf"
- `Section` - e.g., "REQUIREMENT 1: Build Secure Networks"
- `Requirement` - e.g., "1.1 Install and maintain network security controls"
- `SubRequirement` - e.g., "1.1.1 Formal process for approving network connections"

**Neo4J Relationships (Connections):**
- `(Document)-[:BELONGS_TO]->(Framework)`
- `(Section)-[:PART_OF]->(Document)`
- `(Requirement)-[:BELONGS_TO]->(Section)`
- `(SubRequirement)-[:PART_OF]->(Requirement)`
- `(Requirement)-[:REFERENCES]->(Requirement)`
- `(Requirement)-[:DEPENDS_ON]->(Requirement)`

#### Phase 3: Generate Embeddings (Optional - for future semantic search)
**Goal**: Create vector embeddings for semantic search

1. Convert requirement text to embeddings
2. Store embeddings as node properties
3. Later: Use for semantic similarity search

#### Phase 4: Load into Neo4J
**Goal**: Insert all data into Neo4J

1. **Create nodes** for each entity:
   ```cypher
   CREATE (f:Framework {name: "PCI DSS", version: "4.0.1"})
   CREATE (d:Document {name: "PCI-DSS-v4_0_1.pdf", framework: "PCI DSS"})
   CREATE (s:Section {title: "REQUIREMENT 1", number: "1"})
   CREATE (r:Requirement {id: "1.1", text: "Install and maintain..."})
   ```

2. **Create relationships**:
   ```cypher
   MATCH (d:Document {name: "PCI-DSS-v4_0_1.pdf"})
   MATCH (s:Section {number: "1"})
   CREATE (s)-[:PART_OF]->(d)
   ```

3. **Store embeddings** (if generated):
   ```cypher
   MATCH (r:Requirement {id: "1.1"})
   SET r.embedding = [0.123, 0.456, ...]
   ```

---

## Step 3: What We'll Build Next

### Immediate Next Steps:
1. ✅ **Setup Neo4J** (you're doing this now!)
2. **Create Python Script** to parse PDFs
3. **Design Neo4J Schema** (nodes and relationships structure)
4. **Build Ingestion Script** (load parsed data into Neo4J)
5. **Test with PCI DSS document** first
6. **Add FedRAMP** documents

### Tools We'll Use:
- **PyPDF2 or pdfplumber** - PDF parsing
- **python-neo4j** - Connect to Neo4J from Python
- **sentence-transformers** - Generate embeddings (optional)

---

## Current File Structure

```
Compliance Research/
├── documents/
│   ├── pci-dss/
│   │   ├── PCI-DSS-v4_0_1.pdf
│   │   └── PCI_DSS-QRG-v3_2_1.pdf
│   └── fedramp/
│       ├── fedramp-agency-playbook.pdf
│       └── fedramp-sap-training.pdf
├── neo4j-data/          (created by script)
│   ├── data/            (database files - persistent)
│   ├── logs/            (Neo4J logs)
│   ├── import/          (CSV/JSON for import)
│   └── plugins/         (Neo4J plugins)
├── neo4j-podman.sh      (container setup script)
└── GETTING-STARTED.md   (this file)
```

---

## Neo4J GUI - Built-In Browser

**Neo4J already includes a web GUI** (no separate installation needed):
- Access at: http://localhost:7474
- Features:
  - Visual graph visualization
  - Cypher query interface
  - Data exploration
  - Query results visualization
  - Database statistics

**What You Can Do in Neo4J Browser:**
1. **Write Cypher queries** to explore data
2. **Visualize relationships** between requirements
3. **Browse nodes** and their properties
4. **Run queries** like:
   - "Show me all PCI DSS requirements"
   - "Find relationships between PCI DSS and FedRAMP"
   - "Display the hierarchy of sections and requirements"

---

## Useful Commands

### Start Neo4J:
```bash
./neo4j-podman.sh
```

### Stop Neo4J:
```bash
podman stop compliance-neo4j
```

### Start Neo4J again (after stopping):
```bash
podman start compliance-neo4j
```

### View Neo4J logs:
```bash
podman logs -f compliance-neo4j
```

### Access Neo4J Browser:
Open http://localhost:7474 in your web browser

### Remove container (keeps data):
```bash
podman stop compliance-neo4j
podman rm compliance-neo4j
```
(Note: Data in `neo4j-data/` folder is persistent on your Mac)

---

## Next Steps

Once Neo4J is running:
1. ✅ Verify you can access Neo4J Browser
2. ✅ Test with a simple query
3. **Then we'll build**: PDF parsing scripts
4. **Then we'll create**: Neo4J schema and ingestion pipeline
5. **Then we'll load**: PCI DSS and FedRAMP documents

Ready to start? Run the setup script and let me know when Neo4J is running!

