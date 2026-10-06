---
name: general-testing
description: >
  Column validation workflow for a CData JDBC driver table.
  Invoked by /general-testing. Runs SELECT *, then samples important columns,
  then exercises all key SQL operators for each data type found.
  Validates result consistency between filtered and unfiltered results.
---

# Column Validation (`/columns`)

Validates that a CData JDBC driver table returns correct, consistent data for:
- Full `SELECT *`
- Targeted column selects (important/key columns)
- Data-type-aware SQL operator coverage (`=`, `IN`, `!=`, `LIKE`, `>`, `<`, `BETWEEN`, `IS NULL`)

Log is primary evidence. All probes run via JDBC.

---

## Phase 0 — Gather inputs

Ask for **all** of the following before writing any code:

| Input | What to ask for |
|---|---|
| **Connection string** | Full `jdbc:cdata:<driver>://<properties>` including auth |
| **JAR folder path** | Folder with the driver `.jar` and `.lic` file |
| **Table name** | Which table to validate |
| **Baseline scope** | Ask together with the table name: *"Do you want the baseline to query **all** the data, or only the first **1000** rows?"* Recommend **1000** for large tables — it keeps the run fast and all filter tests are built from those rows. Store the answer as `BASELINE_MODE` = `FULL` or `SAMPLE_1000` (if the user gives another number, use it as the limit). |
| **Specific column(s)** *(optional)* | Ask together with the table name: *"Do you want any specific column(s) tested? If so, name them and I will test **all important operators** for each."* Store as `TARGET_COLUMNS` (empty = default coverage). |
| **RSD folder** *(optional)* | Path to `.rsd` files for this table — used to cross-check column metadata |

Verify the JAR folder:
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

**Step 1b — Write the column-validation harness**

```java
import java.sql.*;

public class ColumnValidationTest {
    public static void main(String[] args) throws Exception {
        Class.forName(args[0]);

        String url = args[1];  // connection string (includes Verbosity + LogFile)

        try (Connection conn = DriverManager.getConnection(url)) {
            System.out.println("[CONNECTED]");

            if (args.length > 2) {
                String query = args[2];
                System.out.println("[QUERY] " + query);
                try (Statement st = conn.createStatement();
                     ResultSet rs = st.executeQuery(query)) {
                    ResultSetMetaData md = rs.getMetaData();
                    int cols = md.getColumnCount();

                    // Print header row with column name + type
                    StringBuilder header = new StringBuilder("[HEADER]");
                    for (int i = 1; i <= cols; i++) {
                        header.append("\t")
                              .append(md.getColumnName(i))
                              .append("(").append(md.getColumnTypeName(i)).append(")");
                    }
                    System.out.println(header);

                    int rowCount = 0;
                    while (rs.next() && rowCount < 20) {
                        StringBuilder row = new StringBuilder("[ROW]");
                        for (int i = 1; i <= cols; i++) {
                            String val = rs.getString(i);
                            row.append("\t").append(val == null ? "__NULL__" : val);
                        }
                        System.out.println(row);
                        rowCount++;
                    }
                    System.out.println("[COUNT] " + rowCount);
                } catch (SQLException e) {
                    System.out.println("[SQL_ERR] " + e.getMessage());
                }
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
javac -cp "<jar_folder>/<driver>.jar" ColumnValidationTest.java

# Windows PowerShell
javac -cp "<jar_folder>\cdata.jdbc.<driver>.jar" ColumnValidationTest.java
```

Helper function (PowerShell — reuse across all phases):
```powershell
$probe_n = 0
function Run-Query([string]$tag, [string]$sql) {
    $script:probe_n++
    $logPath = "$env:TEMP\cdata_col_${tag}_$($script:probe_n).log"
    $url2    = $connStr + "Verbosity=5;LogFile=$logPath;"
    $out     = java -cp ".;$jar" ColumnValidationTest $DC $url2 $sql 2>&1
    return @{ Tag=$tag; SQL=$sql; Out=($out -join "`n"); LogPath=$logPath }
}
```

---

## Phase 2 — SELECT * baseline

Run `SELECT *` with no filters and capture the result as the **baseline**. The query depends on the `BASELINE_MODE` the user chose in Phase 0:

```sql
-- BASELINE_MODE = FULL (user wants all the data)
SELECT * FROM <TableName>

-- BASELINE_MODE = SAMPLE_1000 (user wants up to 1000 records — recommended for large tables)
SELECT * FROM <TableName> LIMIT 1000
```

In `SAMPLE_1000` mode the 1000 returned rows are the baseline: all sample values, column inventory and filter tests in later phases are derived from them, so the run stays fast on large tables. Record the mode in the report header (`Baseline: FULL` / `Baseline: first 1000 rows`).

From the result:
1. Record **all column names** and their **data types** reported in `[HEADER]`.
2. Record **baseline row count** from `[COUNT]` (in `SAMPLE_1000` mode this is at most 1000 and is **not** the table's total size).
3. Note any columns that are entirely `__NULL__` in the baseline — these will be skipped in
   operator tests but noted as "no live data observed".
4. Pick up to **5 non-null sample values** per column for use in later phases.

Build a column inventory table:

| Column | JDBC Type | Sample Values (up to 5) | All Null? |
|--------|-----------|------------------------|-----------|
| Id     | VARCHAR   | abc1, abc2, abc3       | No        |
| Name   | VARCHAR   | Alice, Bob             | No        |
| Amount | DOUBLE    | 10.5, 20.0             | No        |
| Status | VARCHAR   | active, inactive       | No        |
| CreatedAt | TIMESTAMP | 2024-01-01T10:00:00 | No      |
| IsActive | BOOLEAN  | true, false            | No        |
| Tags   | VARCHAR   | vip, new               | No        |

---

## Phase 3 — Important-column SELECT

Select only the **important columns** individually to confirm each returns consistent values
with the baseline. Important columns = primary key(s) + any column with a non-null value in
the baseline. Skip entirely-null columns.

For each important column run:
```sql
SELECT <ColumnName> FROM <TableName>
```

**Consistency check**: the values returned must be a subset of the sample values collected
in Phase 2. Flag any mismatch as a `INCONSISTENCY` bug.

Also run a multi-column projection to confirm column interaction:
```sql
SELECT <KeyColumn>, <StringColumn>, <NumericColumn> FROM <TableName>
```

In `SAMPLE_1000` mode, append `LIMIT 1000` to these queries so they are compared against the same rows. Record for each column:
- Row count matches baseline → ✅ CONSISTENT
- Row count differs → ❌ INCONSISTENCY (note the delta)
- Column missing from result → ❌ MISSING COLUMN

---

## Phase 4 — Operator coverage by data type

**Targeted columns:** if the user named `TARGET_COLUMNS` in Phase 0, then for each named column run **every** operator row in the matching 4A–4D table for its data type (all important operators — `=`, `!=`, `IN`, `NOT IN`, `LIKE`, comparison operators, `BETWEEN`, `IS NULL` / `IS NOT NULL`, boolean true/false as applicable), using more than one sample value where available (e.g. a frequent value, a rare value, a boundary value). If a named column does not exist, report it as `❌ MISSING COLUMN` and continue. These columns are also used as the "Column Used" for their type in the 4E coverage matrix. Other columns still get the default coverage below unless the user said to test only the named columns.

For every data type found in Phase 2, run the tests below. Use the sample values from
Phase 2 as test inputs. If no sample value exists for a column, mark the test as
`⚪ SKIPPED (no data)`.

### 4A — String / VARCHAR columns

Use a string column with known sample values (e.g. `Status`, `Name`, `Id`).

| Test | SQL Template | What to verify |
|------|-------------|----------------|
| Equality `=` | `SELECT * FROM T WHERE Col = 'val1'` | Rows returned only have `Col = val1`; count ≤ baseline |
| Inequality `!=` | `SELECT * FROM T WHERE Col != 'val1'` | No row has `Col = val1`; count + equality count ≈ baseline |
| IN list | `SELECT * FROM T WHERE Col IN ('val1','val2')` | Every row has `Col` equal to one of the listed values |
| NOT IN | `SELECT * FROM T WHERE Col NOT IN ('val1')` | No row has `Col = val1` |
| LIKE prefix | `SELECT * FROM T WHERE Col LIKE 'val%'` | Every returned value starts with `val` |
| LIKE contains | `SELECT * FROM T WHERE Col LIKE '%substring%'` | Every value contains the substring |
| IS NULL | `SELECT * FROM T WHERE Col IS NULL` | Every returned row has `Col = __NULL__` |
| IS NOT NULL | `SELECT * FROM T WHERE Col IS NOT NULL` | No returned row has `Col = __NULL__` |

**Consistency rule**: for `=` and `IN`, the union of rows from `Col = val1` and
`Col != val1` must equal the baseline row count. If the sum differs by more than 10%,
flag as `INCONSISTENCY`.

### 4B — Numeric / INT / DOUBLE / DECIMAL columns

Use a numeric column (e.g. `Amount`, `Price`, `Count`). Let `N` = a numeric sample value.

| Test | SQL Template | What to verify |
|------|-------------|----------------|
| Equality `=` | `SELECT * FROM T WHERE Col = N` | All rows have `Col = N` |
| Greater than `>` | `SELECT * FROM T WHERE Col > N` | All rows have `Col > N` |
| Less than `<` | `SELECT * FROM T WHERE Col < N` | All rows have `Col < N` |
| Greater or equal `>=` | `SELECT * FROM T WHERE Col >= N` | All rows have `Col >= N` |
| Less or equal `<=` | `SELECT * FROM T WHERE Col <= N` | All rows have `Col <= N` |
| BETWEEN | `SELECT * FROM T WHERE Col BETWEEN N1 AND N2` | All rows have `N1 <= Col <= N2` |
| IS NULL | `SELECT * FROM T WHERE Col IS NULL` | All rows have `Col = __NULL__` |

### 4C — Date / Timestamp / DateTime columns

Use a date/timestamp column (e.g. `CreatedAt`, `UpdatedAt`). Let `D` = a sample date value
in the format the driver returned it (e.g. `2024-01-01T00:00:00`).

| Test | SQL Template | What to verify |
|------|-------------|----------------|
| Equality `=` | `SELECT * FROM T WHERE Col = 'D'` | All rows have `Col = D` |
| Greater than `>` | `SELECT * FROM T WHERE Col > 'D'` | All rows have `Col` after `D` |
| Less than `<` | `SELECT * FROM T WHERE Col < 'D'` | All rows have `Col` before `D` |
| BETWEEN | `SELECT * FROM T WHERE Col BETWEEN 'D1' AND 'D2'` | All rows fall in range |
| IS NULL | `SELECT * FROM T WHERE Col IS NULL` | All rows null |

If the driver returns a `[SQL_ERR]` for a date format, try alternative formats
(`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS`, epoch ms) and note which one the driver accepts.

### 4D — Boolean columns

Use a boolean column (e.g. `IsActive`, `IsEnabled`).

| Test | SQL Template | What to verify |
|------|-------------|----------------|
| Equality true | `SELECT * FROM T WHERE Col = true` | All rows have `Col = true` |
| Equality false | `SELECT * FROM T WHERE Col = false` | All rows have `Col = false` |
| IS NULL | `SELECT * FROM T WHERE Col IS NULL` | All rows null |

Note: if true+false count ≠ baseline count, flag as `INCONSISTENCY`.

### 4E — Ensure at least one test per data type

After running the above, confirm the coverage matrix:

| Data Type | Column Used | At least one test run? |
|-----------|-------------|------------------------|
| String/VARCHAR | — | ✅ / ⚪ SKIPPED |
| Integer/DOUBLE | — | ✅ / ⚪ SKIPPED |
| Date/Timestamp | — | ✅ / ⚪ SKIPPED |
| Boolean | — | ✅ / ⚪ SKIPPED |

If a data type exists in the schema but no test ran (no sample data), mark as
`⚪ SKIPPED (no live data)` — not a failure.

---

## Phase 5 — Validate results

For every test query in Phase 4, check:

> **`SAMPLE_1000` mode:** the baseline is a sample, not the whole table, so checks 2 and 3 below (count ≤ baseline, `=` + `!=` complement) are **not valid** — filtered queries run against the full table and can legitimately return more rows than the sample. In this mode: pick filter values from the sample, run each filtered query with `LIMIT 1000`, apply checks 1, 4, 5, 6 as normal, and replace checks 2–3 with a **containment check** — every baseline row that satisfies the predicate (evaluated locally on the sample) must also appear in the filtered result when it is run without `LIMIT`/with enough rows; a missing row = `❌ MISSING ROWS`. Mark checks 2–3 as `N/A (sample baseline)` in the report.

1. **Return-value correctness** — does every returned row satisfy the WHERE condition?
   Check the actual values in `[ROW]` lines.
2. **Count plausibility** — is the filtered count ≤ baseline? If filtered count > baseline,
   flag as `❌ COUNT ANOMALY`.
3. **`=` + `!=` complement check** — `count(= val) + count(!= val)` should ≈ `baseline`.
   Tolerance: ±1 row (pagination edge). Larger gap → `⚠️ COMPLEMENT MISMATCH`.
4. **`IN` subset check** — every row's column value must be one of the IN-list values.
5. **LIKE result check** — every row's column value must satisfy the LIKE pattern.
6. **SQL_ERR on valid input** — if the driver throws for a valid operator+type combo,
   classify as `❌ OPERATOR NOT SUPPORTED` and note it.

---

## Phase 6 — Output report

```
═══════════════════════════════════════════════════════════════
Column Validation Report
═══════════════════════════════════════════════════════════════
Driver  : <DriverName> JDBC
Table   : <TableName>    Date: <date>

Baseline SELECT *
  Mode        : FULL  |  SAMPLE_1000 (first 1000 rows — filters validated against the sample)
  Columns     : <N>
  Row count   : <N>   (SAMPLE_1000: rows in the sample, NOT the table's total size)
  Null columns: <list or "none">
  Target cols : <TARGET_COLUMNS or "none — default coverage">

═══ COLUMN CONSISTENCY ════════════════════════════════════════
Column      | Type      | Solo SELECT Count | Baseline Count | Result   (SAMPLE_1000: solo run with LIMIT 1000)
─────────── | ───────── | ─────────────────┤ ──────────────┤ ──────
Id          | VARCHAR   | 25               | 25             | ✅ CONSISTENT
Amount      | DOUBLE    | 24               | 25             | ❌ INCONSISTENCY (delta=1)
...

═══ OPERATOR COVERAGE ════════════════════════════════════════
Type      | Column   | Operator | SQL (abbreviated)              | Rows | Result
───────── | ──────── | ──────── | ────────────────────────────── | ──── | ──────
VARCHAR   | Status   | =        | WHERE Status='active'          | 10   | ✅ PASS   ← target column: all operators run
VARCHAR   | Status   | !=       | WHERE Status!='active'         | 15   | ✅ PASS
VARCHAR   | Status   | IN       | WHERE Status IN('a','b')       | 18   | ✅ PASS
VARCHAR   | Status   | NOT IN   | WHERE Status NOT IN('a')       | 15   | ✅ PASS
VARCHAR   | Status   | LIKE %   | WHERE Status LIKE 'act%'       | 10   | ✅ PASS
VARCHAR   | Status   | IS NULL  | WHERE Status IS NULL           | 0    | ✅ PASS
DOUBLE    | Amount   | =        | WHERE Amount=10.5              | 3    | ✅ PASS
DOUBLE    | Amount   | >        | WHERE Amount>10.0              | 20   | ✅ PASS
DOUBLE    | Amount   | BETWEEN  | WHERE Amount BETWEEN 5 AND 20  | 18   | ✅ PASS
TIMESTAMP | CreatedAt| >        | WHERE CreatedAt>'2024-01-01'   | 12   | ✅ PASS
BOOLEAN   | IsActive | =true    | WHERE IsActive=true            | 14   | ✅ PASS
BOOLEAN   | IsActive | =false   | WHERE IsActive=false           | 11   | ✅ PASS
...

═══ SAMPLE-MODE CHECKS (SAMPLE_1000 only — omit in FULL mode) ═══
Query                          | Count ≤ baseline | = + != complement | Containment (sample rows ⊆ result) | Result
────────────────────────────── | ──────────────── | ───────────────── | ────────────────────────────────── | ──────
WHERE Status='active'          | N/A              | N/A               | 10 of 10 expected rows present     | ✅ PASS
WHERE Amount>10.0              | N/A              | N/A               | 19 of 20 expected rows present     | ❌ MISSING ROWS (1)
Note: counts are not compared with the sample size because filtered queries run against the full table. Containment = every sample row that satisfies the predicate (evaluated locally) must appear in the filtered result.

═══ BUGS ══════════════════════════════════════════════════════
# | Column  | Operator | Description                        | Severity
─ | ──────── | ──────── | ─────────────────────────────────── | ────────
1 | Amount  | (any)    | Solo SELECT count 24 ≠ baseline 25  | Medium
2 | Tags    | LIKE     | SQL_ERR: LIKE not supported on Tags  | Low

═══ COVERAGE MATRIX ═══════════════════════════════════════════
Type      | Tested? | Column Used
───────── | ─────── | ─────────────
VARCHAR   | ✅ Yes  | Status
DOUBLE    | ✅ Yes  | Amount
TIMESTAMP | ✅ Yes  | CreatedAt
BOOLEAN   | ✅ Yes  | IsActive

Summary: PASS <N> / FAIL <N> / SKIPPED <N> / N/A <N>   Bugs: <N>
Baseline mode: <FULL | SAMPLE_1000>   (N/A = count/complement checks not valid on a sample baseline)
═══════════════════════════════════════════════════════════════
```

---

## Appendix — Common failure patterns

| Symptom | What it means |
|---------|---------------|
| `=` returns more rows than baseline | Driver is ignoring the WHERE clause — client-side passthrough bug |
| `count(=val) + count(!=val)` << baseline | Some rows are hidden by both filters — driver-side evaluation inconsistency |
| `IN ('a','b')` returns only rows matching `'a'` | Driver serializes only first IN-list value — partial pushdown bug |
| `LIKE '%foo%'` returns `[SQL_ERR]` | Driver does not support LIKE for this column type — note as operator gap |
| Solo `SELECT <Col>` count differs from `SELECT *` count | Pagination or lazy-loading inconsistency — file as data consistency bug |
| `!= val` returns same rows as `SELECT *` | Driver is not applying `!=` operator — falling back to full fetch |
| Timestamp column rejects ISO 8601 format | Try `YYYY-MM-DD` short form; note accepted format in report |
| Boolean `= true` returns 0 rows but baseline has booleans | Driver may require `= 'true'` (string) or `= 1` (int) — test all variants |
| `IS NULL` returns non-null rows | Driver is not mapping SQL `IS NULL` to an API-level null filter — client-side only |


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

Save-Usage -Cmd "/general-testing" -Drv $DC -Tbl $TABLE
```
