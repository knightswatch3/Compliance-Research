# Neo4J Storage Setup

This directory contains everything needed to run Neo4J for the Compliance Research System.

## Directory Structure

```
storage/
├── Dockerfile             # Custom Neo4J image configuration
├── neo4j-data/           # Persistent storage (created automatically)
│   ├── data/             # Database files
│   ├── logs/             # Neo4J logs
│   ├── import/           # CSV/JSON files for import
│   └── plugins/          # Neo4J plugins (APOC)
└── README.md             # This file
```

## Quick Start

### Build the Neo4J Image

```bash
cd storage
podman build -t compliance-neo4j .
# or
# docker build -t compliance-neo4j .
```

### Run the Container (with volume mounts)

```bash
podman run -d \
  --name compliance-neo4j \
  -p 7474:7474 \
  -p 7687:7687 \
  -v "$(pwd)/neo4j-data/data:/data" \
  -v "$(pwd)/neo4j-data/logs:/logs" \
  -v "$(pwd)/neo4j-data/import:/var/lib/neo4j/import" \
  -v "$(pwd)/neo4j-data/plugins:/plugins" \
  compliance-neo4j
```

These `-v` flags bind the `storage/neo4j-data/` subfolders on your Mac to the corresponding directories inside Neo4J, so the database, logs, import files, and plugins persist outside the container.

To stop and remove the container:
```bash
podman stop compliance-neo4j
podman rm compliance-neo4j
```

(Replace `podman` with `docker` if you are using Docker.)

## Access Neo4J

### Web Browser GUI:
- **URL**: http://localhost:7474
- **Username**: `neo4j`
- **Password**: `compliance123`

### Database Connection:
- **URI**: `bolt://localhost:7687`
- **Username**: `neo4j`
- **Password**: `compliance123`

## Persistent Storage

All Neo4J data is stored on the host in `neo4j-data/`:
- **Database files** → `neo4j-data/data/`
- **Logs** → `neo4j-data/logs/`
- **Import folder** → `neo4j-data/import/`
- **Plugins** → `neo4j-data/plugins/`

Because these are bind-mounted, deleting the container does **not** remove your data. To start fresh, delete or clear these directories manually.

## Verify Neo4J is Running

1. Check container status:
   ```bash
   podman ps
   # or
   docker ps
   ```

2. View logs:
   ```bash
   podman logs -f compliance-neo4j
   # or
   docker logs -f compliance-neo4j
   ```

3. Test in Neo4J Browser:
   Open http://localhost:7474 and run:
   ```cypher
   RETURN "Hello Compliance Research!" as message
   ```

## Configuration

### Change Password

Edit `Dockerfile` and change:
```Dockerfile
ENV NEO4J_AUTH=neo4j/your-new-password
```

Rebuild and rerun the container:
```bash
podman build -t compliance-neo4j .
podman stop compliance-neo4j
podman rm compliance-neo4j
podman run ...
```

### Change Ports

Modify the `podman run` (or `docker run`) command:
```bash
-p YOUR_HTTP_PORT:7474 -p YOUR_BOLT_PORT:7687
```

## Useful Commands

### Rebuild Image After Changes:
```bash
podman build -t compliance-neo4j .
```

### Start Container (existing image):
```bash
podman run -d --name compliance-neo4j ... compliance-neo4j
```

### Stop Container:
```bash
podman stop compliance-neo4j
```

### Remove Container:
```bash
podman rm compliance-neo4j
```

### View Logs:
```bash
podman logs -f compliance-neo4j
```

### Execute Cypher Shell:
```bash
podman exec -it compliance-neo4j cypher-shell -u neo4j -p compliance123
```

## Troubleshooting

### Container Won't Start
1. Check if ports 7474 or 7687 are already in use:
   ```bash
   lsof -i :7474
   lsof -i :7687
   ```

2. Check logs:
   ```bash
   podman logs compliance-neo4j
   ```

### Can't Access Browser
1. Make sure container is running: `podman ps`
2. Try accessing: http://127.0.0.1:7474
3. Check firewall settings

### Data Not Persisting
1. Ensure the `-v` volume mounts are present in the run command
2. Confirm the host directories exist and have appropriate permissions

## Next Steps

Once Neo4J is running:
1. Access the Browser at http://localhost:7474
2. Test with a simple query
3. Proceed with PDF parsing and data ingestion

## Neo4J Ingestion Cheat Sheet

Run these in the Neo4J Browser (http://localhost:7474) in order.

1. **Verify the JSON file**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value
   RETURN value.control_group AS groupId,
          value.group_title   AS title
   LIMIT 5;
   ```

2. **Create the framework node**
   ```cypher
   MERGE (framework:Framework {name: "NIST SP 800-53", revision: "Rev 5"});
   ```

3. **Create control groups and link to framework**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value AS group
   MERGE (g:ControlGroup {id: group.control_group})
   SET g.control_group = group.control_group,
       g.title         = group.group_title,
       g.description   = group.group_description,
       g.purpose       = group.group_purpose,
       g.tags          = group.group_tags
   MERGE (framework:Framework {name: "NIST SP 800-53", revision: "Rev 5"})
   MERGE (g)-[:BELONGS_TO]->(framework);
   ```

4. **Create controls and connect to their groups**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value AS group
   UNWIND group.controls AS control
   MATCH (g:ControlGroup {id: group.control_group})
   MERGE (c:Control {control_id: control.control_id})
   SET c.title       = control.control_title,
       c.description = control.control_description,
       c.tags        = control.control_tags
   MERGE (c)-[:IN_GROUP]->(g);
   ```

5. **Attach rules to each control**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value AS group
   UNWIND group.controls AS control
   MATCH (c:Control {control_id: control.control_id})
   WITH c, control.rules AS rules
   UNWIND coalesce(rules, []) AS rule
   MERGE (r:Rule {rule_id: rule.id})
   SET r.text     = rule.text,
       r.type     = rule.type,
       r.platform = rule.platform,
       r.tool     = rule.tool,
       r.tags     = rule.tags
   MERGE (c)-[:HAS_RULE]->(r);
   ```

6. **Add rule dependencies**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value AS group
   UNWIND group.controls AS control
   UNWIND coalesce(control.rules, []) AS rule
   MATCH (r:Rule {rule_id: rule.id})

   FOREACH (depRuleId IN coalesce(rule.dependent_rule_ids, []) |
     MERGE (depRule:Rule {rule_id: depRuleId})
     MERGE (r)-[:DEPENDS_ON_RULE]->(depRule)
   )

   FOREACH (depCtrlId IN coalesce(rule.dependent_control_ids, []) |
     MERGE (depCtrl:Control {control_id: depCtrlId})
     MERGE (r)-[:DEPENDS_ON_CONTROL]->(depCtrl)
   );
   ```

7. **Link related controls**
   ```cypher
   CALL apoc.load.json("file:///Master.json") YIELD value AS group
   UNWIND group.controls AS control
   MATCH (c:Control {control_id: control.control_id})
   FOREACH (relId IN coalesce(control.related_controls, []) |
     MERGE (related:Control {control_id: relId})
     MERGE (c)-[:RELATES_TO]->(related)
   );
   ```

Use these commands whenever you refresh `Master.json` to rebuild the graph. Already-existing nodes/relationships will be merged rather than duplicated.

