---
name: cdata-qa
description: >
  Unified CData JDBC driver QA skill covering both API-backed and database-backed drivers.
  Triggers on any /command below, or when user mentions a workflow by name (e.g. "run filter
  pushdown", "test auth", "CUD testing", "stored procedure QA", "column validation",
  "performance analysis", "QueryPassThrough", "db driver testing", "QPT testing").

  API driver commands (REST/SOAP sources):
    /filter          — server-side filter pushdown validation (single-column + AND/OR combination testing;
                       RSD validation only when an RSD is provided — dynamic tables have none)
    /auth            — AuthScheme + PRP end-to-end validation
    /cud             — INSERT / UPDATE / DELETE testing (API drivers)
    /sp              — stored procedure validation (RSB+metadata dual discovery, negative type tests)
    /general-testing — column/data validation: SELECT * baseline (all rows or first 1000), operators by data type,
                       optional target columns with all important operators
    /perf            — performance analysis: 15-check log pattern detection
    /usage           — show cumulative token usage and cost report across all sessions (auto-logged per command; in-chat markdown summary + terminal box)

  DB driver commands — QPT=True (SQL forwarded verbatim to DB engine):
    /qpttrue create   /qpttrue insert   /qpttrue select
    /qpttrue update   /qpttrue delete

  DB driver commands — QPT=False (CData SQL engine rewrites before forwarding):
    /qptfalse create   /qptfalse insert   /qptfalse select
    /qptfalse update   /qptfalse delete

  Always read the matching workflow file before starting any workflow.
---

# CData QA — Unified Skill

> **Version:** 1.1.0 — see [CHANGELOG.md](CHANGELOG.md) for what changed.

---

## 🚀 How to use this skill

This skill covers two types of CData JDBC drivers. Follow the guide for your type.

---

### Type 1 — API Drivers (REST / SOAP / cloud)

These drivers connect to third-party APIs (Salesforce, Shopify, Instagram, Square, etc.)
and use RSD/RSB schema files.

**Step 1 — Pick your command:**

| What you want to test | Command |
|-----------------------|---------|
| Filters pushed to API correctly (WHERE clauses, incl. AND/OR combinations) | `/filter` |
| OAuth / AuthScheme / PRP connection properties | `/auth` |
| INSERT / UPDATE / DELETE operations | `/cud` |
| Stored procedures (RSB files, endpoint routing) | `/sp` |
| Column data validation, all operators by type | `/general-testing` |
| API call count, N+1, LIMIT pushdown, pagination | `/perf` |

**Step 2 — Type the command**, e.g. `/filter`

**Step 3 — Claude will ask for:**
- Connection string: `jdbc:cdata:<driver>://<auth_properties>`
- JAR folder path (must contain `.jar` + `.lic`)
- Any workflow-specific inputs (table name, RSB folder, etc.)

**Step 4 — Claude runs everything autonomously** and prints a report.

---

### Type 2 — DB Drivers (SQL Server, MySQL, PostgreSQL, Oracle, SQLite, etc.)

These drivers connect to relational databases. They have a `QueryPassThrough` connection
property that controls whether SQL is sent to the DB engine verbatim or processed first
by CData's SQL rewriter.

**What is QueryPassThrough?**
- **QPT=True** — SQL is forwarded to the DB exactly as written. The DB engine executes it.
  Use this to verify the driver does not silently modify your SQL.
- **QPT=False** — CData's SQL engine parses and may rewrite your SQL before forwarding.
  Use this to verify CData's rewriter produces semantically identical results.

**Step 1 — Decide which mode to test:**

| Goal | Mode | Commands |
|------|------|---------|
| Verify driver forwards SQL unchanged | QPT=True | `/qpttrue <op>` |
| Verify CData SQL rewriter is correct | QPT=False | `/qptfalse <op>` |
| Test both modes for a full comparison | Both | Run QPT=True first, then QPT=False |

**Step 2 — Pick your operation:**

| Operation | QPT=True command | QPT=False command |
|-----------|-----------------|-------------------|
| CREATE TABLE (all data types, schema check) | `/qpttrue create` | `/qptfalse create` |
| INSERT (types, nulls, constraints, bulk) | `/qpttrue insert` | `/qptfalse insert` |
| SELECT (all operators, ORDER BY, LIMIT, aggregates, subqueries) | `/qpttrue select` | `/qptfalse select` |
| UPDATE (per type, expression, bulk, no-WHERE guard) | `/qpttrue update` | `/qptfalse update` |
| DELETE (by key/filter/IN/subquery, no-WHERE guard) | `/qpttrue delete` | `/qptfalse delete` |

**Step 3 — Recommended full test order (for a new driver):**

```
/qpttrue create   →  /qpttrue insert   →  /qpttrue select
                  →  /qpttrue update   →  /qpttrue delete

/qptfalse create  →  /qptfalse insert  →  /qptfalse select
                  →  /qptfalse update  →  /qptfalse delete
```

Each operation re-uses `$testTable` and `$insertedIds` from earlier steps — run them
in this order within the same session.

**Step 4 — Claude will ask for:**
- JAR folder path (must contain `.jar` + `.lic`)
- Connection string (without `QueryPassThrough` — Claude adds it per mode)
- DB engine type: SQL Server / MySQL / PostgreSQL / Oracle / SQLite
- Database / schema name

**Step 5 — Claude runs all test cases autonomously** and prints a report after each operation.

---

### Unsure which driver type?

| Signal | Type |
|--------|------|
| Connection string has `Server=` or `Database=` pointing to a DB host | DB driver → use `/qpt*` commands |
| User mentions `QueryPassThrough` | DB driver |
| Connection string uses OAuth, API key, or connects to a cloud app | API driver → use `/filter`, `/auth`, etc. |
| Driver uses `.rsd` / `.rsb` schema files | API driver |
| Not sure | Claude will ask: "Is this connecting to a database or a REST/cloud API?" |

---

## Command → file mapping

### API driver commands

| Command | Workflow | File |
|---------|----------|------|
| `/filter` | Filter pushdown validation: per-column pushdown, multi-column AND/OR combination testing, RSD cross-check only if an RSD is provided (static `<table>.rsd`, semi-dynamic `<table>{internal}.rsd`; dynamic tables have no RSD) | `workflows/filter.md` |
| `/auth` | AuthScheme + PRP validation | `workflows/auth.md` |
| `/cud` | CUD testing (API drivers) | `workflows/cud.md` |
| `/sp` | Stored procedure validation | `workflows/sp.md` |
| `/general-testing` | General column/data validation — asks upfront for baseline scope (all data or first 1000 rows, recommended for large tables) and optional specific column(s) to test with all important operators; sample mode uses containment checks instead of count checks | `workflows/general-testing.md` |
| `/perf` | Performance analysis | `workflows/perf.md` |
| `/usage` | Token/cost usage report | Read `workflows/token-tracker.md`, run `Show-UsageReport`, then parse the `CLAUDE_USAGE_JSON:` line from output and present the in-chat markdown summary as described in that file. No Phase 0, no driver connection. |

### DB driver — QPT=True commands

| Command | File |
|---------|------|
| `/qpttrue create` | `workflows/db-shared-setup.md` → `workflows/db-create-qpt.md` |
| `/qpttrue insert` | `workflows/db-shared-setup.md` → `workflows/db-insert-qpt.md` |
| `/qpttrue select` | `workflows/db-shared-setup.md` → `workflows/db-select-qpt.md` |
| `/qpttrue update` | `workflows/db-shared-setup.md` → `workflows/db-update-qpt.md` |
| `/qpttrue delete` | `workflows/db-shared-setup.md` → `workflows/db-delete-qpt.md` |

### DB driver — QPT=False commands

| Command | File |
|---------|------|
| `/qptfalse create` | `workflows/db-shared-setup.md` → `workflows/db-create-qptfalse.md` |
| `/qptfalse insert` | `workflows/db-shared-setup.md` → `workflows/db-insert-qptfalse.md` |
| `/qptfalse select` | `workflows/db-shared-setup.md` → `workflows/db-select-qptfalse.md` |
| `/qptfalse update` | `workflows/db-shared-setup.md` → `workflows/db-update-qptfalse.md` |
| `/qptfalse delete` | `workflows/db-shared-setup.md` → `workflows/db-delete-qptfalse.md` |

---

---

## Global rules (apply to EVERY command)

### 1. Destructive operation gate
Before executing any operation that could irreversibly modify or delete data, Claude
**must pause and ask the user for explicit confirmation**. This includes:
- Any UPDATE or DELETE without a WHERE clause
- DELETE that would affect more than one row (e.g. via IN list with many IDs)
- DROP TABLE
- Any CUD operation on a table the user has not explicitly named as a test/scratch table
- Any `/sp` call whose RSB marks the SP as destructive (DELETE/CANCEL/ARCHIVE)

**Ask:** "This will [describe what will happen]. Confirm you want to proceed? (yes/no)"

Do not proceed until the user replies "yes" or equivalent. If the user replies "no" or
does not reply, skip the test and log it as `SKIPPED (user did not confirm)`.

This gate applies in ALL workflows — `/cud`, `/sp`, `/qpttrue delete`, `/qptfalse delete`,
`/qpttrue update` (no-WHERE), and any ad-hoc SQL the user asks Claude to run.

### 2. Token & cost tracking
Every workflow **must** call `Record-Usage` at its final step (see `workflows/token-tracker.md`).
The `Record-Usage` function:
- Is defined in `db-shared-setup.md` for DB workflows
- Must be copy-pasted into Phase 0 of each API driver workflow
- Records command, driver, table/SP, model, actual token counts (from real API call), and cost to `~/.cdata-qa/usage-log.json`

**Partial-run logging:** If a workflow exits early (user cancels, destructive gate declined, fatal error), Claude must still call `Record-Usage` with `[PARTIAL]` appended to SessionSummary. Never silently drop a run.

`/usage` reads `~/.cdata-qa/usage-log.json`, prints a terminal summary box **and** presents a formatted markdown table in chat. Claude parses the `CLAUDE_USAGE_JSON:` line from the PowerShell output to build the in-chat summary. If the file does not exist yet, Claude says so and explains that it is created automatically when any workflow finishes.

### 3. Execution model — applies to EVERY command, EVERY workflow

**Never spawn a subagent. Never use SubagentHandback.** All phases of every workflow run inline in the current Claude context, whether invoked standalone or inside this merged skill.

**Web search — global rule for ALL workflows and ALL phases:**
- **Tool:** Use **Claude's built-in web search only** (the native capability). Do NOT use any external MCP server, plugin, browser tool, or added search connector. All searches run inline in the current Claude context. This applies to every workflow file without exception.
- **Snippet-first:** One targeted search → read the snippet. If the snippet contains the needed information (parameter names, types, endpoint details) — **stop. Use the snippet. Do not fetch the page or spec file.**
- **Fetch only if necessary:** Only call web_fetch if the snippet is truncated and the required data is missing. When fetching, extract only the specific section needed (e.g. the parameters block for one endpoint) and discard the rest from context immediately.
- **No re-fetching:** Never re-fetch a URL already retrieved in this session.
- **OpenAPI/Swagger specs:** Search for the endpoint's parameters first. If the snippet has the param list — stop, do not fetch the spec file. If the snippet is insufficient, fetch only the matching `paths` block for that endpoint, not the full spec.

This rule overrides any instruction inside a workflow file that says "fetch the documentation page", "fetch the spec file directly", or similar — treat those as "search for and extract the relevant section using the built-in search."

### 4. Required param client-side enforcement (SP and CUD workflows)
For any param declared `IsRequired=true` in sys_procedureparameters or `xs:required="true"` in
an RSB file, the driver must reject the call at the driver level — **no HTTP request should fire**
— for all of: missing, empty string `''`, `NULL`, whitespace-only `'   '`. Any HTTP request
in the log for these cases is a bug to file.

---

## Routing rules

1. **Detect the command** — match `/command` at the start of the message (case-insensitive).
   If absent but the user describes a workflow by name, map to the nearest command and confirm.

2. **API commands** — read the single matching `workflows/<name>.md` and follow its Phase 0. Execute all phases inline as Claude — do NOT spawn a subagent or use SubagentHandback. All web searches and file reads must run in the current Claude context.

3. **DB commands** — always read `workflows/db-shared-setup.md` first (defines `$testTable`,
   `$typeMap`, `Run-DB`, `Add-Result`, `Cleanup-TestTable`), then read the matching
   `workflows/db-<op>-qpt.md` or `workflows/db-<op>-qptfalse.md` file.
   - Operations share state: `$testTable` and `$insertedIds` carry forward within a session.
   - If starting mid-sequence (e.g. `/qpttrue select` without having run create+insert),
     ask the user for the table name and skip to Phase S0.

4. **`/usage`** — read `workflows/token-tracker.md`. Run `Show-UsageReport` (PowerShell terminal box). Then find the `CLAUDE_USAGE_JSON:` line in the output and present the **in-chat markdown summary** as specified in `token-tracker.md → Step 2`. No inputs, no driver connection.

5. **`/token-tracker`** — this is NOT a valid command. Tell the user: "/token-tracker is an internal module. Type /usage to see your token and cost report."

6. **`/help` or `/commands`** — print the usage guide above and stop.

7. **Unknown command** — list available commands and ask which the user wants.

---

## Shared conventions (all workflows)

- **Log-first verdict**: CData `Verbosity=5` log is primary evidence. JVM stdout is secondary.
- **Discover class name**: always `jar tf <driver>.jar | grep -i Driver.class` — never guess.
- **LIC check**: stop and warn if no `.lic` file present in the JAR folder.
- **OS-aware log path**: Windows → `System.getenv("TEMP")`; Linux/Mac → `System.getProperty("java.io.tmpdir")`.
- **Fresh log per probe**: unique log file per test case, never reused.
- **BOM-safe Java writes** (PowerShell): `[System.IO.File]::WriteAllText(..., [System.Text.Encoding]::ASCII)`.
- **Autonomous execution**: run all commands yourself; never show commands for the user to run.
- **Destructive tests gated**: no-WHERE UPDATE/DELETE skipped unless `$userConfirmedDestructive=$true`.
