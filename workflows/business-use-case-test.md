# Business Use-Case Test (`/usecase`)

Turns a table's metadata plus real-world customer scenarios into **natural-language questions → SQL**,
runs each SQL through the JDBC driver, and validates three things for every query:

1. **Request** — is what the driver sent to the API correct and efficient? (server-side filter, projection, LIMIT, paging, call count)
2. **Response** — what did the server actually return, and does it match what the driver received?
3. **Result** — do the rows shown by the driver correctly answer the business question?

These are the same questions a customer would ask the driver directly, or ask an AI agent through an
MCP server (Connect AI / CData MCP) that generates SQL against the driver.

**Read-only.** Only `SELECT` is ever run. Never print credentials. Never run an unbounded query on a large table.

---

## Token & cost rules (apply throughout)

- Fetch metadata **once**, keep only `ColumnName, DataType, IsKey` (drop everything else from context).
- **At most 3 web searches** (Phase 2), then stop. Summarise to ≤ 10 bullet lines; do not paste pages.
- Generate **10–12 test cases** (default), not an exhaustive matrix. Ask the user for more only if they want it.
- Every query has a `LIMIT` (default 50). Never print more than **3 rows** per query into the chat.
- **Never print a whole log.** Extract only the HTTP request lines and status/count lines (Phase 5 snippets).
- Store per-query results as one compact line in a scratch file; build the report from that file at the end.
- Reuse the JDBC harness, `Run-Query` helper and connection steps from `perf.md` / `general-testing.md`
  Phase 1 — do not rewrite them.
- Do not re-run a query that already PASSed; re-run only failures once to confirm.

---

## Phase 0 — Gather inputs

Ask for all of these in a single message:

| Input | What to ask for |
|---|---|
| **Connection string** | Full `jdbc:cdata:<driver>://<properties>` including auth |
| **JAR folder path** | Folder with the driver `.jar` and `.lic` |
| **Table / entity** | One table to generate use-cases for (e.g. `Accounts`) |
| **Business context** *(optional)* | Industry or persona (e.g. "support manager", "finance") — sharpens the scenarios |
| **Test-case count** *(optional)* | Default 10–12 |
| **Row limit per query** *(optional)* | Default `LIMIT 50` |

Verify the JAR folder has exactly one `.jar` and one `.lic`, discover the driver class, compile the harness
and define `Run-Query` exactly as in `general-testing.md` Phase 1 (Steps 1a–1c). Always add
`Verbosity=5;LogFile=<path>;` to the connection string so the request/response can be inspected.

---

## Phase 1 — Read table metadata

```sql
SELECT ColumnName, DataType, IsKey FROM sys_tablecolumns WHERE TableName = '<table>'
```

If the driver rejects those column names, run `SELECT * FROM sys_tablecolumns LIMIT 1` once to learn the
real names (see `cud.md` for the remap pattern), then re-run. If the table is not found, list candidates with
`SELECT TableName FROM sys_tables WHERE TableName LIKE '%<part>%'` and ask the user.

Classify columns once and keep only this compact summary:

| Group | Columns |
|---|---|
| Key / id | … |
| Status / enum-like (string with few values) | … |
| Date / datetime | … |
| Numeric / amount | … |
| Boolean | … |
| Foreign keys / references (`*Id`) | … |
| Free text (name, email, description) | … |

Then pull a **tiny live sample** to get real values for tests (one query, 5 rows, only the columns you will
filter on): `SELECT <status>, <date>, <fk>, <name> FROM <table> LIMIT 5`. Use these real values in the SQL — a test
built on a value that does not exist proves nothing.

---

## Phase 2 — Research (bounded)

Goal: learn **what real customers ask of this entity in this product**, not generic SQL.

1. Identify the vendor and the entity's business meaning (1 search): `"<Vendor> <entity> API list filter fields"`.
2. Find common customer scenarios (1–2 searches): `"<Vendor> <entity> report common use cases"`,
   `"<Vendor> <entity> MCP server example prompts"`.
3. From the results record only: **filterable fields the API documents**, **common business questions**, and any
   **API limits** (page size max, rate limit, date format). Stop after 3 searches.

Then combine with your own knowledge of the domain. Write a **research digest of ≤ 10 lines** and keep only that.

Scenario families to draw from (pick those that fit the entity):

| Family | Example business question → SQL shape |
|---|---|
| Status / lifecycle | "Which accounts are deactivated?" → `WHERE Status = 'deactivated'` |
| Time window | "What was created in the last 30 days?" → `WHERE CreatedDate >= '<date>'` |
| Time range | "Invoices due this quarter" → `WHERE DueDate BETWEEN … AND …` |
| Ownership / relationship | "Everything owned by user X" → `WHERE OwnerId = '<id>'` |
| Threshold / amount | "Deals over 10 000" → `WHERE Amount > 10000` |
| Search by name/email | "Find customers named Acme" → `WHERE Name LIKE 'Acme%'` |
| Multi-condition | "Active high-value accounts in EMEA" → `WHERE Status='active' AND Amount>… AND Region='EMEA'` |
| Either/or | "Open or pending tickets" → `WHERE Status IN ('open','pending')` / `OR` |
| Top-N / latest | "10 most recent orders" → `ORDER BY CreatedDate DESC LIMIT 10` |
| Existence / missing data | "Contacts without an email" → `WHERE Email IS NULL` |
| Count / aggregate | "How many tickets per status?" → `GROUP BY Status` with `COUNT(*)` |
| Narrow projection | "Just names and emails" → `SELECT Name, Email …` (tests column projection) |
| Lookup by key | "Show account 001…" → `WHERE Id = '<id>'` |
| Negative / empty | Value that does not exist → expect 0 rows, no error |

---

## Phase 3 — Generate the test plan

Produce a table of **10–12 cases** covering at least: one status/enum, one date window, one key lookup, one
`IN`, one multi-condition `AND`, one `OR`, one narrow projection, one Top-N/`ORDER BY`, one `IS NULL`, one
aggregate (if the entity supports it), and one negative case. Use real values from the Phase 1 sample.

```
Business Use-Case Test Plan — <Driver> — <Table>
Research digest: <≤10 lines>

| ID  | Business question (natural language)         | SQL                                              | Expected request behaviour           | Expected result            |
|-----|----------------------------------------------|--------------------------------------------------|--------------------------------------|----------------------------|
| U01 | Which accounts are deactivated?              | SELECT Id, Name, Status FROM Accounts WHERE …    | status filter in URL/body            | all rows Status=deactivated|
| U02 | Which accounts were created in last 30 days? | … WHERE CreatedDate >= '2026-…'                  | date param in request                | all rows within window     |
```

Rules for the plan:

- Every SQL has a `LIMIT` (aggregates excepted) and selects **only the columns the question needs**, except one
  `SELECT *` case.
- State, per case, what you expect the **driver to push to the server** (from API docs where known, per
  `filter.md` §4e: the API docs decide what is pushable, not the RSD or the driver).
- Mark any case where the API cannot filter server-side as `Expected: client-side (documented)` so it is not
  reported as a defect.
- **Show the plan and wait for the user's confirmation** before running anything. Apply their edits.

---

## Phase 4 — Run each test

For each case, in order:

1. Clear/choose a fresh log path (`Run-Query` already creates `cdata_col_<tag>_<n>.log`).
2. Run the SQL through `Run-Query`.
3. Keep from the output only: `[COUNT]`, `[SQL_ERR]`, and the **first 3 `[ROW]` lines**.
4. Immediately extract the compact evidence from the log (Phase 5), then discard the log text.

Run independent cases back-to-back in one shell call; do not pause between them.

---

## Phase 5 — Validate request, response and result

### 5a — Extract evidence (small snippets only)

```powershell
# Outgoing requests (URL / method) — nothing else
Select-String -Path $LogPath -Pattern 'GET |POST |PUT |PATCH ' | ForEach-Object { $_.Line.Trim() } | Select-Object -First 15
# Response status and size/record hints
Select-String -Path $LogPath -Pattern 'HTTP/1\.[01] \d{3}|Content-Length|"total|"count|nextPage|next_cursor|NextLink|has_more' | ForEach-Object { $_.Line.Trim() } | Select-Object -First 15
```

```bash
grep -E 'GET |POST |PUT |PATCH ' "$LOG" | head -15
grep -E 'HTTP/1\.[01] [0-9]{3}|Content-Length|"total|"count|nextPage|next_cursor|NextLink|has_more' "$LOG" | head -15
```

For SOAP, look inside `<soap:Body>` as described in `filter.md` §5b. If a call's body/URL is not logged
(very large payload), record `evidence unavailable` rather than guessing.

### 5b — Request validation (what the driver sent)

| # | Check | PASS when |
|---|---|---|
| R1 | **Filter pushdown** (see `filter.md` §5b) | Every pushable WHERE condition appears in the URL/body using the **API param name** from the docs; value format correct (date format, enum casing, list separator) |
| R2 | **No wrong/dropped condition** | For `AND`/`OR`/`IN`, nothing was silently dropped; an `OR` the API cannot express is **not** pushed as only one side |
| R3 | **Projection** | If the SQL selects a few columns and the API supports field selection (`fields=`, `$select`, `columns`), it is used; otherwise note "API has no projection" |
| R4 | **LIMIT pushdown** (`perf.md` Check 6) | `effective_limit = MIN(sql_limit, api_max)` in the request; page size not larger than needed |
| R5 | **Early pagination exit** (`perf.md` Check 7) | Stops after enough rows for the LIMIT; no further `next` calls |
| R6 | **Call count** | 1 call for a simple filtered query; > 1 is justified only by paging or a documented need. Flag N+1 (`perf.md` Phase 5) |
| R7 | **Correct endpoint** (`perf.md` Check 1) | The endpoint is the one the docs give for this entity/filter (e.g. no full-list call when a by-id endpoint exists) |
| R8 | **Sort pushdown** | `ORDER BY … LIMIT n` uses an API sort param when documented; otherwise client-side is acceptable and noted |
| R9 | **No redundant/duplicate calls** (`perf.md` Checks 11–12) | No repeated identical request |
| R10 | **Aggregate handling** | `COUNT`/`GROUP BY` uses an API count/aggregate when one exists; otherwise client-side and noted |

Classify undocumented params the driver sends as `UNDOCUMENTED` (see `filter.md` §4g), not PASS/FAIL.

### 5c — Response validation (what the server returned vs what the driver surfaced)

| # | Check | PASS when |
|---|---|---|
| S1 | **HTTP status** | 2xx on every call; 4xx/5xx → record the status and message; a 4xx caused by a param the driver sent is a **driver defect** |
| S2 | **Record count agrees** | Records in the server response (log) ≈ rows the driver returned (allow for client-side filtering/LIMIT); a big gap with no client-side filter = data loss |
| S3 | **Field mapping** | For one returned row, the key fields' values in the raw response match the JDBC values (id, status, date, amount) — no truncation, wrong type, or timezone shift |
| S4 | **Paging completeness** | If the server reported more data (`has_more`/`next`) and the SQL had no LIMIT, the driver followed it; with a LIMIT it stopped correctly |
| S5 | **Empty-set behaviour** | For a valid filter with no matches: 0 rows and no error |
| S6 | **Error surfacing** | When the server rejects a request, the driver raises a clear error rather than returning silent empty data |

### 5d — Result validation (does the answer fit the question)

| # | Check | PASS when |
|---|---|---|
| V1 | **Predicate holds** | Every returned row satisfies the full WHERE clause (check the 3 sampled rows and, where cheap, a `COUNT(*)` of violating rows via a second tiny query) |
| V2 | **Question answered** | The returned columns/rows actually answer the natural-language question (right columns, sensible values) |
| V3 | **Count consistency** | `COUNT(*)` with the same WHERE equals the number of rows when under the LIMIT; `A AND B` ≤ each alone; `A OR B` ≥ each alone |
| V4 | **Order and limit** | `ORDER BY` respected in the 3 sampled rows; row count ≤ LIMIT |
| V5 | **Real-data sanity** | Values look like the real entity (no all-null columns where data exists, no obviously swapped columns) |

### 5e — Verdict per test

| Verdict | Meaning |
|---|---|
| **PASS** | R, S and V checks all pass |
| **PARTIAL** | Correct rows, but an efficiency check failed (e.g. filter done client-side though API supports it; no LIMIT pushdown; N+1) |
| **FAIL** | Wrong/missing rows, dropped condition, driver error on valid SQL, or request rejected by server due to driver-sent param |
| **DOCUMENTED LIMIT** | API cannot do it server-side/at all and the driver handles it as documented |
| **UNDOCUMENTED** | Works via a param not in the API docs (fragile) |
| **SKIPPED** | No usable data/value for this case (say why) |

Write one compact line per test to the scratch file, e.g.
`U03|PASS|calls=1|pushed=status,limit|rows=12|note=`

---

## Phase 6 — Report

Keep it short. Share this in chat (HTML/Excel only if the user asks):

```
Business Use-Case Test — <Driver> — <Table>      Date: <date>
Research digest: <≤5 lines>
Summary: PASS <n> / PARTIAL <n> / FAIL <n> / DOC-LIMIT <n> / UNDOC <n> / SKIPPED <n>   API calls total: <n>

| ID  | Business question | SQL (abbrev.) | Request | Response | Result | Verdict | Notes |
|-----|-------------------|---------------|---------|----------|--------|---------|-------|
| U01 | Deactivated accts | WHERE Status='deactivated' | ✅ pushed ?status=… (1 call) | ✅ 200, 12 recs | ✅ 12 rows match | PASS | |
| U02 | Created last 30d  | WHERE CreatedDate>=…       | ⚠ not pushed (full scan, 9 calls) | ✅ 200 | ✅ correct | PARTIAL | API supports created_after |
```

Then, **only for PARTIAL / FAIL / UNDOCUMENTED rows**, one short finding each:

```
### U02 — <question> — PARTIAL
SQL: <full SQL>
Request (actual): <URL/body, 1–3 lines>
Expected: <what the docs say>
Server response: <status / count>
Driver result: <rows, first mismatch if any>
Issue: <one sentence>   Severity: High (wrong data) / Medium (inefficient) / Low
Suggested fix: <one sentence>
```

Close with **Customer / MCP impact** (3–5 lines): which business questions work well through the driver or an
MCP-connected agent, which work but are slow (client-side filtering, many calls), and which fail — written in
business terms, not driver internals.

---

## Appendix — Common patterns

| Symptom | Likely cause |
|---|---|
| Status question returns all rows, then filters | Column not marked filterable / param not mapped — missed pushdown |
| Date question issues many page calls | Date filter not pushed; driver pages the full table |
| `OR` returns only one side's rows | Driver pushed half the `OR` — dropped-condition bug |
| `LIKE` returns 0 rows though data exists | API only supports exact/prefix match; driver passes wrong wildcard/format |
| Narrow `SELECT` is as slow as `SELECT *` | No field projection pushed to the API |
| `ORDER BY … LIMIT 10` returns unsorted rows | Sort not pushed and LIMIT applied before client-side sort |
| 400/422 on a valid-looking filter | Wrong param name or value format sent by the driver |
| Row count in server response ≫ driver rows with no WHERE | Truncation or paging cut-off |

---

## Final step — record actual token usage

Run the same `Save-Usage` block as the **Final step** in `general-testing.md`, with
`Save-Usage -Cmd "/usecase" -Drv $DC -Tbl $TABLE`, after the report is printed.
