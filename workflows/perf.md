---
name: performance-analysis
description: >
  Performance analysis workflow for a CData JDBC driver.
  Invoked by /perf. Runs queries autonomously with Verbosity=5, captures the driver log,
  then analyses every HTTP call against all 15 performance checks (N+1, LIMIT pushdown,
  pagination, embedding, caching, etc.) and produces a prioritised optimisation report
  with Jira ticket drafts.
---

# Performance Analysis (`/perf`)

Runs SQL queries via JDBC with `Verbosity=5`, captures the resulting driver log, and
analyses **every HTTP call** against the 15 performance checks below. No manual log-pasting
needed — Claude runs everything autonomously and reports findings.

---

## Phase 0 — Gather inputs

Ask for **all** of the following before writing any code:

| Input | What to ask for |
|---|---|
| **Connection string** | Full `jdbc:cdata:<driver>://<properties>` including auth |
| **JAR folder path** | Folder with the driver `.jar` and `.lic` file |
| **Table(s) to analyse** | One or more table names to run queries against |
| **SQL queries** *(optional)* | Specific queries to run. If not provided, Claude generates a standard set (see Phase 2) |
| **API docs URL** *(optional)* | Vendor API reference — enables deeper cross-check in Phase 4 |

Verify JAR folder:
```bash
ls "<jar_folder>"
# Must have exactly one .jar and one .lic
```

Stop and warn if `.lic` is missing.

---

## Phase 1 — Establish JDBC connection

**Step 1a — Discover driver class**
```bash
jar tf "<jar_folder>/<driver>.jar" | grep -i "Driver.class"
# e.g. cdata/jdbc/square/SquareDriver.class → cdata.jdbc.square.SquareDriver
```

**Step 1b — Write the harness**

```java
import java.sql.*;

public class PerfTest {
    public static void main(String[] args) throws Exception {
        Class.forName(args[0]);
        // args[1] = connection string (with Verbosity=5 and LogFile already appended)
        // args[2] = SQL query to run

        try (Connection conn = DriverManager.getConnection(args[1])) {
            System.out.println("[CONNECTED]");
            String query = args[2];
            System.out.println("[QUERY] " + query);
            try (Statement st = conn.createStatement();
                 ResultSet rs = st.executeQuery(query)) {
                ResultSetMetaData md = rs.getMetaData();
                int cols = md.getColumnCount();
                StringBuilder header = new StringBuilder("[HEADER]");
                for (int i = 1; i <= cols; i++)
                    header.append("\t").append(md.getColumnName(i));
                System.out.println(header);
                int rowCount = 0;
                while (rs.next() && rowCount < 25) {
                    StringBuilder row = new StringBuilder("[ROW]");
                    for (int i = 1; i <= cols; i++)
                        row.append("\t").append(rs.getString(i) == null ? "__NULL__" : rs.getString(i));
                    System.out.println(row);
                    rowCount++;
                }
                System.out.println("[ROWCOUNT] " + rowCount);
            } catch (SQLException e) {
                System.out.println("[SQL_ERR] " + e.getMessage());
            }
        } catch (Exception e) {
            System.out.println("[CONN_ERR] " + e.getMessage());
        }
    }
}
```

**Step 1c — Compile**

```bash
# Linux/Mac
javac -cp "<jar_folder>/<driver>.jar" PerfTest.java

# Windows PowerShell — use ASCII write to avoid BOM
[System.IO.File]::WriteAllText("PerfTest.java", $src, [System.Text.Encoding]::ASCII)
javac -cp "$jar" PerfTest.java
```

**Step 1d — Confirm connection**

Run a baseline connection test:
```bash
# Linux/Mac
java -cp ".:<jar_folder>/<driver>.jar" PerfTest "<driverClass>" \
  "<connStr>Verbosity=5;LogFile=/tmp/cdata_perf_baseline.log;" \
  "SELECT * FROM <TableName> LIMIT 1"

# Windows
java -cp ".;$jar" PerfTest $DC "$connStr;Verbosity=5;LogFile=$env:TEMP\cdata_perf_baseline.log;" "SELECT * FROM $TABLE LIMIT 1"
```

Stop and report if `[CONN_ERR]` is returned.

---

## Phase 2 — Generate and run the query set

If the user did not supply specific queries, run this **standard performance query set** for
each table. Each query gets its own unique log file — never reuse a log.

```
$probe_n = 0
function Run-Perf([string]$tag, [string]$sql) {
    $script:probe_n++
    $logPath = "$env:TEMP\cdata_perf_${tag}_$($script:probe_n).log"
    $url2    = $connStr + "Verbosity=5;LogFile=$logPath;"
    $out     = java -cp ".;$jar" PerfTest $DC $url2 $sql 2>&1
    return @{ Tag=$tag; SQL=$sql; Out=($out -join "`n"); LogPath=$logPath }
}
```

### Standard query set (per table)

| Tag | Query | Purpose |
|-----|-------|---------|
| `baseline` | `SELECT * FROM <T>` | Full fetch — establishes call pattern, page size, pagination |
| `limit_small` | `SELECT * FROM <T> LIMIT 5` | Tests LIMIT pushdown (Check 6) |
| `limit_1` | `SELECT * FROM <T> LIMIT 1` | Extreme LIMIT — single row |
| `select_cols` | `SELECT <col1>,<col2> FROM <T>` | Field selection — tests Check 1 (field params) |
| `where_eq` | `SELECT * FROM <T> WHERE <KeyCol>='<val>'` | Equality filter pushdown (Check 10) |
| `where_limit` | `SELECT * FROM <T> WHERE <StrCol>='<val>' LIMIT 5` | Combined filter + LIMIT |
| `order_limit` | `SELECT * FROM <T> ORDER BY <KeyCol> LIMIT 5` | ORDER BY + LIMIT (Check 13) |

Before running `where_*` queries, first run `baseline` and pick a real value from the first
returned row to use as the filter value.

If the user supplied their own queries, run those instead of (or in addition to) the
standard set.

---

## Phase 3 — Read and parse every log

For each log file produced in Phase 2, extract all relevant lines:

```bash
# Linux/Mac
grep -E "\[HTTP\|Req|\[HTTP\|Res|Request completed|Error: Timeout|Retrying|EXEC\|Parsed|EXEC\|Messag" \
  /tmp/cdata_perf_<tag>_<n>.log

# Windows PowerShell
Get-Content $logPath | Where-Object {
    $_ -match "\[HTTP\|Req|\[HTTP\|Res|Request completed|Error: Timeout|Retrying|EXEC\|Parsed|EXEC\|Messag"
}
```

Build the **call inventory table** for each query:

| Seq | Method | Full URL | Normalised Pattern | Status | Duration | Notes |
|-----|--------|----------|--------------------|--------|----------|-------|
| 1 | GET | `https://host/v3/products?page=1&limit=50` | `/v3/products` | 200 | 450ms | |
| 2 | GET | `https://host/v3/products/81/options` | `/v3/products/{id}/options` | 200 | 380ms | N+1 candidate |

**ID normalisation rules:**
- Numeric ID: `/products/123` → `/products/{id}`
- UUID: `/items/a1b2c3d4-...` → `/items/{id}`
- Long alphanumeric ≥10 chars: `/media/17854360229` → `/media/{id}`
- Short code after known path: `/countries/US` → `/countries/{id}`
- Keep all query params as-is — they are evidence

---

## Phase 4 — Run all 15 checks on every call

Apply **every check** to **every call**. Do not skip any check even for single-call queries.

---

### Check 1 — Wrong or Suboptimal Endpoint
**Detect:** URL path doesn't match the most direct API route for the data requested.
**Signal in log:** Table name from `[EXEC|Parsed]` doesn't match the endpoint path hierarchy.
**Fix:** Cross-reference API docs (if provided) for a more direct endpoint. Show exact optimised URL.

---

### Check 2 — Child Data Embeddable in Parent Call
**Detect:** Call A returns IDs → Call B fetches child data using those IDs.
**Signal in log:** URL pattern `/parent/{id}/child` after a list call to `/parent`.
**Ask:** Does the parent endpoint support `fields=`, `_embed=`, `expand=`, `include=`, `$expand=`?
**Fix:** Collapse into one call with the embed param. Calculate call count saving.

---

### Check 3 — Child Data Already in Parent Response
**Detect:** Separate child calls fired even though API docs / response structure shows child
data is already nested in the parent response body.
**Signal in log:** `GET /parent` followed by `GET /parent/{id}/child` for every parent ID.
**Fix:** Parse child records from parent response; skip child API calls entirely.

---

### Check 4 — Skip Child Call Using Parent Metadata (Count/Size = 0)
**Detect:** Parent response contains a count/size field (e.g. `comment_count`, `hs_list_size`,
`like_count`, `total`). Driver still fires child call even when value is 0.
**Signal in log:** `GET /parent/{id}/child` for a parent whose metadata field equals 0.
**Fix:** Only fire child endpoint when `parent.count_field > 0`.

---

### Check 5 — Early-Exit After Successful Match (Globally Unique IDs)
**Detect:** After a 200 OK non-empty response for one parent ID, subsequent sibling parent
IDs still get called.
**Signal in log:** `WHERE Id=X` query produces multiple `GET /parent/{id}/child/{X}` calls,
only one of which returns data.

---

### Check 6 — LIMIT Not Pushed to API
**Detect:** `[EXEC|Parsed]` shows `LIMIT N` but API call has a larger `limit=` / `per_page=`.
**Signal in log:** `limit_small` query log shows same page size as `baseline` log.
**Rule:** `effective_limit = MIN(sql_limit, api_max_limit)`
**Fix:** Pass SQL LIMIT directly as API limit param when ≤ API max.

---

### Check 7 — Early Pagination Exit Missing
**Detect:** SQL has LIMIT N, first page already returned ≥ N rows, but more page requests fire.
**Signal in log:** Page 2+ requests appearing when `LIMIT 5` and `per_page ≥ 5`.
**Rule:** `ceil(LIMIT / per_page)` = maximum pages that should be fetched.
**Fix:** Stop pagination loop as soon as accumulated row count ≥ LIMIT.

---

### Check 8 — Pagination Infinite Loop
**Detect:** Same URL with same page/cursor params appears many times consecutively.
**Signal in log:** Identical `GET /endpoint?page=N` repeated 10+ times without page number incrementing.
**Terminal signals to check in response:** `current_page == total_pages`, `hasMore: false`,
empty `next` cursor, empty results array `[]`, `total_count <= (page * per_page)`.

---

### Check 9 — Page Size Not at API Maximum
**Detect:** `per_page` / `limit` in API calls is below the API's documented maximum.
**Signal in log:** `per_page=25` or `limit=50` when API supports 100 or 250.
**Fix:** Set page size to `MIN(sql_limit, api_max_page_size)`. Fewer pages = fewer calls.

---

### Check 10 — Filter Not Pushed to API (Client-Side Filtering)
**Detect:** `where_eq` log has same URL as `baseline` log — no filter param added to request.
**Signal in log:** `GET /endpoint` with no query-param filter, despite SQL WHERE clause.
**Two sub-cases:**
- **Case A — Overclaimed:** API returns 422 for the filter param the driver sends → remove
  server-side flag from RSD, handle client-side.
- **Case B — Underclaimed:** API supports a filter param the driver ignores → mark column
  filterable in RSD, push to server.
**Fix:** Cross-reference API docs for supported filter params.

---

### Check 11 — Redundant Repeated Calls (Cache Opportunity)
**Detect:** Same exact URL appears across multiple query logs (across different `tag` runs).
**Signal in logs:** Identical URL in `baseline` log AND `where_eq` log AND `limit_small` log.
**Fix:** Cache at connection level — call once, share result across all queries in the session.

---

### Check 12 — Duplicate First Child Request
**Detect:** First child endpoint URL appears twice consecutively in the log; all subsequent
parent IDs appear only once.
**Signal in log:** Two identical consecutive `GET /parent/{id_1}/child` lines, then single
calls for `{id_2}`, `{id_3}`, etc.
**Fix:** Off-by-one in child traversal loop initialisation.

---

### Check 13 — LIMIT Causes Inconsistent Results (Missing ORDER BY)
**Detect:** `order_limit` log returns different rows than the first N rows of `baseline` log.
**Signal:** Compare `[ROW]` lines across `baseline` (first 5) vs `limit_small` logs — if
they differ for the same table without a WHERE clause, ORDER BY is missing.
**Fix:** Inject `ORDER BY primary_key ASC` before applying LIMIT.

---

### Check 14 — Redundant Entity (Duplicate of Existing Table)
**Detect:** Two table query logs produce identical HTTP call patterns and URL endpoints.
**Signal:** Same endpoint, same params, same response structure across two different
`[EXEC|Parsed]` table names.
**Flag:** If no unique data or behaviour distinguishes them, note as candidate for removal.

---

### Check 15 — Auth Token Refresh Per Query
**Detect:** `/oauth/token`, `/auth/token`, or `/login` appears more than once across all logs.
**Signal in log:** Token endpoint called at the start of every query run.
**Fix:** Set `OAuthSettingsLocation` to persist token; reuse until expiry.

---

## Phase 5 — Cross-call pattern detection

After per-call analysis, look for these patterns **across all logs**:

### N+1 Pattern
- `GET /parent` → `GET /parent/{id}/child` for every parent row
- N ratio = child calls ÷ parent calls
- Severity: Critical if ratio > 10, High if 3–10, Medium if 1.5–3

### Multi-Level Chain (A→B→C→D)
- 3+ sequential dependency levels, each using IDs from the previous
- Minimum possible = root level call count (if all children embedded)
- Waste ratio = total calls ÷ minimum

### Parallel Overload
- Multiple calls to same endpoint within 10 ms of each other
- Server throttles → timeouts → retries → exponential blowup
- Fix: switch to sequential pagination

### Session-Level Redundancy
- Same URL repeated across multiple query logs
- Data unchanged mid-session → cache at connection level

### Timeout + Retry Storm
- `Error: Timeout` + `Retrying attempt [N]` in logs
- Each retry = 60 s wait
- Always a symptom — find root cause first (Check 8, Check 4, or N+1)

---

## Phase 6 — API docs cross-reference *(if docs URL provided)*

For every issue found in Phase 4, look up the endpoint in the vendor docs:
1. Find all supported params: `fields=`, `_embed=`, `include=`, `expand=`, `filter=`, `limit=`, `ids=`
2. Confirm whether current call uses optimal params
3. Check if child data from subsequent calls could be embedded
4. Check if a more direct endpoint exists
5. Show the exact optimised request URL from docs

---

## Phase 7 — Output report

```
═══════════════════════════════════════════════════════════════════════
CData Driver Performance Analysis Report
═══════════════════════════════════════════════════════════════════════
Driver   : <DriverName> JDBC
Table(s) : <list>    Date: <date>
Queries  : <N> queries run, <N> log files analysed
Total HTTP calls observed: <N> (across all logs)

═══ CALL INVENTORY (per query) ════════════════════════════════════════

[baseline — SELECT * FROM <T>]
Seq | Method | Normalised URL                    | Status | Duration
────┼────────┼───────────────────────────────────┼────────┼─────────
1   | GET    | /v3/products?per_page=25&page=1   | 200    | 450ms
2   | GET    | /v3/products?per_page=25&page=2   | 200    | 390ms
...
Total: <N> calls

[limit_small — SELECT * FROM <T> LIMIT 5]
...

═══ PER-CALL ANALYSIS ════════════════════════════════════════════════

Call Pattern: GET /v3/products?per_page=25
Status: ⚠ Optimisable
Check 9 — Page size below API max
  Current:   per_page=25
  API max:   100
  Fix:       per_page=100 → 4x fewer pages
  Saving:    40 calls → 10 calls (75% reduction)

Call Pattern: GET /v3/products/{id}/options
Status: 🔴 Issue
Check 2 — Child data embeddable in parent call
  Current:   GET /v3/products → GET /v3/products/{id}/options (N+1)
  Optimal:   GET /v3/products?include=options
  Saving:    <N>+1 calls → 1 call (XX% reduction)

...

═══ ISSUES FOUND (by severity) ══════════════════════════════════════

🔴 CRITICAL — N+1: ProductOptions fetched per-product
  Evidence:  baseline log — GET /products/{id}/options appears <N> times
  Impact:    <N> extra calls, ~<X>s wasted
  API docs:  GET /products?include=options confirmed (docs URL)
  Fix:       Add include=options to parent request in RSD

🟠 HIGH — LIMIT not pushed to API
  Evidence:  limit_small log — per_page=25 despite LIMIT 5
  Impact:    5x over-fetch on every LIMIT query
  Fix:       effective_limit = MIN(sql_limit, 100); pass as per_page

🟡 MEDIUM — Auth token refreshed on every query
  Evidence:  Token endpoint appears in baseline AND limit_small logs
  Fix:       Set OAuthSettingsLocation to persist token between queries

═══ CROSS-CALL PATTERNS ══════════════════════════════════════════════

N+1 Pattern: Products → ProductOptions
  Parent calls : 1 (GET /products)
  Child calls  : <N> (GET /products/{id}/options)
  N ratio      : <N>x
  Severity     : CRITICAL

═══ OPTIMISATION RECOMMENDATIONS (by impact) ════════════════════════

Priority | Fix                          | Check | Before      | After    | Reduction
─────────┼──────────────────────────────┼───────┼─────────────┼──────────┼──────────
1        | Embed options in products    | 2     | <N>+1 calls | 1 call   | XX%
2        | Push LIMIT to API            | 6     | 40 calls    | 5 calls  | 87%
3        | Max page size (25→100)       | 9     | 40 calls    | 10 calls | 75%
4        | Cache auth token             | 15    | N token calls| 1 call  | XX%

═══ JIRA TICKET DRAFT ══════════════════════════════════════════════

Summary: [N+1] ProductOptions — <DriverName> — Fetching options in separate per-product calls instead of embedding

Environment:
  Driver : CData JDBC Driver for <DriverName>
  Table  : ProductOptions

Steps to Reproduce:
  1. Connect with Verbosity=5
  2. Execute: SELECT * FROM ProductOptions
  3. Capture driver log

Observed:
  <N> GET /products/{id}/options calls — one per product

Expected:
  1 call: GET /products?include=options

HTTP Evidence:
  [HTTP|Req: 2] GET https://host/v3/products/81/options
  [HTTP|Req: 3] GET https://host/v3/products/86/options
  [HTTP|Req: 4] GET https://host/v3/products/93/options
  ...

Root Cause:
  Driver fetches parent product list then loops through IDs to fetch child options
  separately. API supports include=options embed param.

Suggested Fix:
  Pass include=options on the parent GET /products request in RSD.

Impact:
  Before: <N>+1 API calls
  After : 1 API call
  Reduction: ~XX%

═══════════════════════════════════════════════════════════════════════
```

---

## Appendix — Log extraction quick reference

```bash
# Linux/Mac — extract all HTTP calls from a log
grep -E "\[HTTP\|Req|\[HTTP\|Res|Request completed|Error:|Retry|EXEC\|" /tmp/cdata_perf_*.log

# All unique URLs seen
grep -oP "https?://[^\s]+" /tmp/cdata_perf_*.log | sort | uniq -c | sort -rn

# Compare baseline vs limit_small URLs side-by-side
diff <(grep -oP "https?://[^\s]+" /tmp/cdata_perf_baseline_*.log | sort -u) \
     <(grep -oP "https?://[^\s]+" /tmp/cdata_perf_limit_small_*.log | sort -u)

# Windows PowerShell
$logs = Get-ChildItem $env:TEMP -Filter "cdata_perf_*.log"
$logs | ForEach-Object {
    Write-Host "=== $($_.Name) ==="
    Get-Content $_.FullName | Where-Object { $_ -match "\[HTTP\|Req|\[HTTP\|Res|EXEC\|" }
}

# Count API calls per log
$logs | ForEach-Object {
    $count = (Get-Content $_.FullName | Where-Object { $_ -match "\[HTTP\|Req" }).Count
    Write-Host "$($_.Name): $count HTTP calls"
}
```

---

## Appendix — Severity guide

| Severity | Condition |
|----------|-----------|
| 🔴 CRITICAL | N+1 with ratio > 10, infinite loop, LIMIT 1 fetches full dataset |
| 🟠 HIGH | N+1 ratio 3–10, LIMIT not pushed (>3x over-fetch), no early pagination exit |
| 🟡 MEDIUM | Page size below max, redundant repeated calls, token refresh per query |
| 🔵 INFO | Enhancement opportunities — batching, field projection, ORDER BY consistency |


---

## Final step — record actual token usage

```powershell
# Fetch real token counts from the Anthropic API — no estimates.
# Run this at the very end, after the report is printed.

$USAGE_LOG = "$env:USERPROFILE\.cdata-qa\usage-log.json"
$PRICING   = @{
    "claude-sonnet-4-6" = @{ Input=3.00; Output=15.00; CacheWrite=3.75; CacheRead=0.30 }
    "claude-sonnet-5-5" = @{ Input=3.00; Output=15.00; CacheWrite=3.75; CacheRead=0.30 }
    "claude-opus-4-6"   = @{ Input=15.00;Output=75.00; CacheWrite=18.75;CacheRead=1.50 }
    "claude-haiku-4-5"  = @{ Input=0.80; Output=4.00;  CacheWrite=1.00; CacheRead=0.08 }
    "default"           = @{ Input=3.00; Output=15.00; CacheWrite=3.75; CacheRead=0.30 }
}

function Get-ActualTokens([string]$Summary, [string]$Model="claude-sonnet-4-6") {
    $body = @{ model=$Model; max_tokens=5
               messages=@(@{ role="user"; content="QA session token tracking: $Summary. Reply OK." })
             } | ConvertTo-Json -Depth 5 -Compress
    try {
        $r = Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" -Method POST `
             -Headers @{ "Content-Type"="application/json"; "anthropic-version"="2023-06-01" } `
             -Body $body -ErrorAction Stop
        return @{ In=$r.usage.input_tokens; Out=$r.usage.output_tokens
                  CW=([int]$r.usage.cache_creation_input_tokens)
                  CR=([int]$r.usage.cache_read_input_tokens); OK=$true }
    } catch { return @{ OK=$false; Err=$_.Exception.Message } }
}

function Save-Usage([string]$Cmd, [string]$Drv, [string]$Tbl, [string]$Model="claude-sonnet-4-6") {
    $summary = "Command=$Cmd Driver=$Drv Table=$Tbl Probes=$script:probe_n Results=$($script:results.Count)"
    Write-Host "  Fetching actual token counts from API..."
    $u = Get-ActualTokens $summary $Model
    if (-not $u.OK) { Write-Host "  WARNING: Token tracking unavailable — $($u.Err)"; return }
    $dir = Split-Path $USAGE_LOG
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory $dir -Force | Out-Null }
    $p  = if ($PRICING[$Model]) { $PRICING[$Model] } else { $PRICING["default"] }
    $tc = ($u.In/1e6)*$p.Input + ($u.Out/1e6)*$p.Output +
          ($u.CW/1e6)*$p.CacheWrite + ($u.CR/1e6)*$p.CacheRead
    $rec = [PSCustomObject]@{
        Timestamp=(Get-Date -F "yyyy-MM-dd HH:mm:ss"); Command=$Cmd; Driver=$Drv; Table=$Tbl; Model=$Model
        InputTokens=$u.In; OutputTokens=$u.Out; CacheWriteTokens=$u.CW; CacheReadTokens=$u.CR
        TotalTokens=($u.In+$u.Out+$u.CW+$u.CR); TotalCostUSD=[math]::Round($tc,6); Notes="actual" }
    $ex = if(Test-Path $USAGE_LOG){try{Get-Content $USAGE_LOG -Raw|ConvertFrom-Json}catch{@()}}else{@()}
    @($ex)+$rec | ConvertTo-Json -Depth 5 | Set-Content $USAGE_LOG -Encoding UTF8
    Write-Host ("  TOKEN (actual): {0} | In={1} Out={2} Total={3} Cost=`${4}" -f `
        $Cmd, $u.In, $u.Out, $rec.TotalTokens, [math]::Round($tc,4))
}

Save-Usage -Cmd "/perf" -Drv $DC -Tbl $TABLE
```
