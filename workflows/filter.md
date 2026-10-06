---
name: server-side-filter-pushdown-validation
description: >
  CData JDBC driver filter pushdown validation. Triggers on /filter or when user mentions
  "filter pushdown", "server-side evaluation", "WHERE clause pushed to API", "path param",
  "query param pushdown", "validate APIP file", or "check RSD claims".
---

# Server-Side Filter Pushdown Validation

This skill validates whether a CData JDBC driver correctly pushes SQL `WHERE` clause conditions down to the remote API — as path parameters, query parameters, or POST body fields — rather than fetching all records and filtering client-side.

---

## Phase 0 — Gather inputs

Before starting, collect all three of the following. Do not proceed until all are confirmed.

| Input | What to ask for | Notes |
|---|---|---|
| **Connection string** | `jdbc:cdata:<driver>://<properties>` | Must include all auth properties needed to connect |
| **Driver name** | e.g. `Instagram`, `WooCommerce`, `Apify` | Used to locate the correct JAR |
| **JAR folder path** | Folder containing the driver `.jar` and `.lic` file | Both must be present in the same folder; warn if `.lic` is missing |

**Verify JAR folder** before writing any code:
- List the folder and confirm exactly one `.jar` matching the driver name and exactly one `.lic` file are present.
- If the `.lic` is missing, stop and tell the user — the driver will silently fail or throw a license error.

---

## Phase 1 — Establish JDBC connection

**Step 1a — Discover the driver class name**

Do not guess the class name. Always inspect the JAR first:

```bash
jar tf "<jar_folder>/<driver>.jar" | grep -i "Driver.class"
```

The output will show the actual class path, e.g.:
```
cdata/jdbc/twilio/TwilioDriver.class
```

Convert the path to a fully-qualified class name by replacing `/` with `.` and removing `.class`:
```
cdata.jdbc.twilio.TwilioDriver
```

Common naming patterns (verify, don't assume):
- `cdata.jdbc.<driver>.<DriverName>Driver`  — e.g. `cdata.jdbc.twilio.TwilioDriver`
- `cdata.jdbc.<driver>.CData<DriverName>Driver` — older drivers

**Step 1b — Write the test harness**

Write a minimal Java test harness using only the standard JDBC API. Do not use any CData-specific classes directly — load the driver reflectively.

```java
import java.sql.*;

public class FilterPushdownTest {
    public static void main(String[] args) throws Exception {
        Class.forName("<driver_class_from_step_1a>");

        String url = "<connection_string_from_user>"
                   + "Verbosity=5;LogFile=" + System.getenv("TEMP") + "\\cdata_filter_test.log;";

        try (Connection conn = DriverManager.getConnection(url)) {
            System.out.println("Connected successfully.");

            if (args.length > 0) {
                String query = args[0];
                System.out.println("Running query: " + query);
                try (Statement st = conn.createStatement();
                     ResultSet rs = st.executeQuery(query)) {
                    ResultSetMetaData md = rs.getMetaData();
                    int cols = md.getColumnCount();
                    for (int i = 1; i <= cols; i++) {
                        System.out.print(md.getColumnName(i) + (i < cols ? "\t" : "\n"));
                    }
                    int rowCount = 0;
                    while (rs.next()) {
                        for (int i = 1; i <= cols; i++) {
                            System.out.print(rs.getString(i) + (i < cols ? "\t" : "\n"));
                        }
                        rowCount++;
                    }
                    System.out.println("--- " + rowCount + " row(s) returned ---");
                }
            }
        }
    }
}
```

**Key notes on the log file path:**
- On Windows, use `System.getenv("TEMP")` in the Java code — do NOT hardcode `C:\cdata_filter_test.log` as the driver process may not have write access to the root of C:\.
- On Linux/Mac, use `System.getProperty("java.io.tmpdir")` or hardcode `/tmp/cdata_filter_test.log`.

**Step 1c — Compile and run**

On Windows (PowerShell):
```powershell
$jar = "C:\path\to\driver.jar"
javac -cp "$jar" FilterPushdownTest.java
java -cp ".;$jar" FilterPushdownTest
```

On Linux/Mac:
```bash
javac -cp "/path/to/driver.jar" FilterPushdownTest.java
java  -cp ".:/path/to/driver.jar" FilterPushdownTest
```

**If connection fails:**
- Confirm the JAR and LIC are in the same folder
- Re-check the class name from Step 1a — it is the most common cause of `ClassNotFoundException`
- Verify connection string properties (AuthScheme, credentials, etc.)

---

## Phase 2 — Ask which table to test

Once connected, ask: **"Which table would you like to validate filter pushdown for?"**

Wait for the user's answer before continuing. The rest of this skill is scoped to that one table name.

---

## Phase 3 — Baseline query: identify the endpoint

Run a `SELECT * FROM <table> LIMIT 5` to capture the driver log. This tells you:
1. Which API endpoint the driver calls for this table
2. What the base HTTP request URL looks like with no filters applied

The connection string already includes `Verbosity=5` and `LogFile` from Step 1b. Clear the log before each run so results are unambiguous.

**Query to run:**
```sql
SELECT * FROM <table> LIMIT 5;
```

**Extracting the outgoing URL from the log (Windows PowerShell):**
```powershell
$log = "$env:TEMP\cdata_filter_test.log"
Get-Content $log | Where-Object { $_ -match "GET |POST " }
```

**On Linux/Mac:**
```bash
grep -E "GET |POST " /tmp/cdata_filter_test.log
```

Look for a line like:
```
[HTTP|Req: 0] GET https://api.example.com/v1/resource?PageSize=5
```

Note the full URL — this is your unfiltered baseline for comparison in Phase 5.

---

## Phase 3b — Discover column-to-API-field mapping (required for dynamic tables)

This step is **mandatory for dynamic tables** (API Profile / `.apip` connectors where no RSD exists) and optional but recommended for static tables when you suspect column name mismatches.

### Why this matters

CData JDBC column names often differ from the API's JSON field names. For example:
- API returns `{"createdAt": "2024-01-01"}` → JDBC column may be `created_at`
- API returns `{"Created-At": "..."}` → driver may normalize to `created_at`

If you run `WHERE created_at = '...'` but the API param is `createdAt`, knowing this mapping upfront tells you what to look for in the outgoing HTTP URL during Phase 5.

### The approach — compare JDBC resultset vs raw API response

You already ran `SELECT * FROM <table> LIMIT 5` in Phase 3. The Verbosity=5 log captures both the outgoing request and the raw API response body. Compare them side by side:

**Step 1 — Extract JDBC column names from the resultset output**

The test harness already prints column headers. Note every column name printed.

**Step 2 — Extract the raw API response JSON from the log**

```powershell
# Windows
Get-Content "$env:TEMP\cdata_filter_test.log" | Where-Object { $_ -match "Response|Body|<response" }
```

```bash
# Linux/Mac
grep -i "response\|body" /tmp/cdata_filter_test.log
```

If the full response body isn't in the log, the Verbosity=5 output may have truncated it. In that case, make a direct API call to the same endpoint using curl or Postman with the same credentials to get the raw JSON.

**Step 3 — Build the mapping table**

Compare JSON keys from the API response against JDBC column names and document each mapping:

```
Column mapping — <TableName>

| JDBC Column Name | API JSON Key  | Notes                          |
|------------------|---------------|--------------------------------|
| created_at       | createdAt     | snake_case → camelCase         |
| id               | id            | identical                      |
| friendly_name    | FriendlyName  | snake_case → PascalCase        |
```

### Caveats

| Situation | How to handle |
|---|---|
| Nested JSON (`meta.createdAt`) | Driver may flatten to `meta_createdAt` or just `createdAt` — check the actual JDBC column name in the resultset |
| Driver normalizes all names to lowercase/snake_case | Account for this pattern when comparing — `Created-At` → `created_at` is normalization, not a bug |
| Large response truncated in log | Make a direct API call with curl/Postman to get the full raw JSON |
| Field exists in response but not in JDBC resultset | Driver may have excluded it — it won't be filterable via JDBC regardless |

**Important:** this step tells you the column ↔ field mapping. It does **not** confirm whether that field is wired as a filterable query param. That confirmation happens in Phase 5.

### Report all mapping issues to the user before continuing

After building the mapping table, check every row for the issues below and **report all findings to the user in one block before moving to Phase 4**. Do not silently proceed if any issue is found — the user needs to know because each issue may affect whether a filter test in Phase 5 produces a valid result.

| Issue | How to detect | What to tell the user |
|---|---|---|
| **JDBC column name differs from API JSON key** | `JDBC Column` ≠ `API JSON Key` in the mapping table | "Column `<jdbc_name>` maps to API field `<api_key>`. When checking pushdown in Phase 5, look for `<api_key>` in the URL, not `<jdbc_name>`." |
| **API JSON key not found in JDBC resultset** | API response has a field that doesn't appear as any JDBC column | "API field `<key>` is present in the API response but missing from the JDBC resultset. This field cannot be tested for filter pushdown until the driver exposes it as a column." |
| **JDBC column not found in API response** | JDBC column doesn't match any key in the API JSON (even after normalization) | "JDBC column `<name>` has no matching field in the API response. Its mapping source is unclear — may be a computed/virtual column or a driver-side alias. Flag for developer review." |
| **Multiple API fields map to the same JDBC column** | Two JSON keys normalize to the same column name (e.g. `created_at` and `createdAt` both → `created_at`) | "Ambiguous mapping: API fields `<key1>` and `<key2>` both resolve to JDBC column `<name>`. Only one will be pushed — verify which one the driver actually uses." |
| **Nested JSON field flattened without clear separator** | API has `{"meta": {"createdAt": "..."}}` and JDBC column name doesn't make the path obvious | "Column `<name>` appears to be flattened from a nested API field. Confirm the exact API param path before testing pushdown — nested fields may not be filterable at the top level." |

If no issues are found, say: "Mapping looks clean — all JDBC columns have a direct match to API JSON keys. Proceeding to Phase 4."

---

## Phase 3c — RSD extraction (all table types — always run)

**This phase runs for every table, regardless of whether the driver uses an APIP file, a built-in RSD folder, or any other schema source. Never skip this phase.**

- **APIP file:** extract from the `.apip` ZIP archive (see Step 3c-1 below).
- **Built-in RSD folder (e.g. HubSpot, Salesforce JDBC):** read the `.rsd` file directly from the driver's `db/<schema>/` folder. The user's RSD folder path is either stored in memory or should be asked for once and saved.
- **No accessible RSD:** note that and proceed — Phase 4 (API docs) is still mandatory and the query plan still comes from there.

---

### What this phase does — and what it does NOT do

This phase only **extracts** the RSD from the APIP file and records its claims. It does **not** validate those claims yet. Validation happens in Phase 3d, which runs **after** Phase 4.

The reason: the API docs are the ground truth. The RSD is what someone claimed the API supports. You must fetch the docs first, establish what is actually true, and only then check whether the RSD matches. If you compare RSD claims before reading the docs, you risk treating the RSD as correct and testing only what it says — which is exactly the wrong approach.

**Correct order:**
```
Phase 3c — Extract RSD claims (read only, no validation yet)
     ↓
Phase 4  — Fetch API docs (OpenAPI / WSDL / human docs) — THIS is the ground truth
     ↓
Phase 3d — Compare RSD claims against API docs → find discrepancies
     ↓
Phase 4e — Generate SQL query plan from API docs (not from RSD)
     ↓
Phase 5  — Execute and validate
```

---

### Step 3c-1 — Locate the RSD for the target table

**For APIP files** — the `.apip` file is a ZIP archive. Extract it to find the RSD files:

```powershell
# Windows PowerShell
$apipPath = "<path_to_file.apip>"
$extractPath = "$env:TEMP\apip_extracted"
Expand-Archive -Path $apipPath -DestinationPath $extractPath -Force
Get-ChildItem $extractPath -Recurse -Filter "*.rsd" | Select-Object FullName
```

```bash
# Linux/Mac
APIP_PATH="<path_to_file.apip>"
EXTRACT_PATH="/tmp/apip_extracted"
mkdir -p "$EXTRACT_PATH"
unzip -o "$APIP_PATH" -d "$EXTRACT_PATH"
find "$EXTRACT_PATH" -name "*.rsd"
```

Locate the `.rsd` file whose name matches the table being tested (e.g. `Accounts.rsd`, `Contacts.rsd`). If no exact match, look for a partial match or check inside subdirectories.

**For built-in RSD folder drivers** — read the `.rsd` file directly using the Read tool. The path is `<rsd_folder>\<TableName>.rsd`. If the table has no dedicated RSD (e.g. it uses a shared internal RSD like `PipelinesInternal.rsd`), read the internal RSD instead — the driver's own `sys_tablecolumns` query or a previous baseline run will reveal which backing RSD applies.

---

### Step 3c-2 — Parse the RSD and extract all claims

Read the RSD file and extract the following into a structured list:

**A — Endpoint claim**
```xml
<api:info title="..." desc="..." connection="..." />
```
Look for `<api:set attr="URI" value="..." />` or similar — this is the endpoint the RSD claims the table maps to.

Also look for:
- `<api:set attr="Method" value="GET/POST/PUT/..." />` — HTTP method claimed
- Any `{PathParam}` placeholders in the URI value — these are claimed path parameters

**B — Column claims**
For each `<attr name="..." xs:type="..." xs:filterable="..." ... />` entry, extract:

| Column name | xs:type | xs:filterable | xs:key | xs:readonly | api:set mapping (if present) |
|---|---|---|---|---|---|
| Id | string | true | true | true | URI path param `{Id}` |
| Status | string | true | false | false | query param `Status` |
| CreatedAt | datetime | true | false | false | query param `created_after` |

Also note:
- `xs:filterable="true"` — column claimed as server-side filterable
- `xs:key="true"` — column claimed as primary key / path param
- `api:set attr="..."` — the actual API param name the column maps to
- `xs:type` — declared data type

**Flag every column whose `xs:type` is `date`, `datetime`, or `timestamp`.** These columns always get the full D1–D6 operator matrix in §4e — `>`, `<`, `>=`, `<=`, `=`, and `BETWEEN` — regardless of what the RSD claims for `filterable` AND regardless of whether `other:supportedOperators` is set on that column. Even if the column is not marked filterable and has no `supportedOperators` attribute, all six date operators must be tested. Mark them clearly in the extracted claims table with a `DATE` tag so they are not missed when building the query plan.

**C — Include param claim (if present)**
Look for any `<api:set>` that references a multi-value or include-style query param (e.g. `ids`, `include`, `filter[id]`). Note if present.

---

### Step 3c-3 — What to do after extracting

Do not validate anything yet. Store the RSD claims table from Step 3c-2 and proceed to **Phase 4** to fetch the API docs. Validation of every RSD claim happens in **Phase 3d** after Phase 4 completes.

---

## Phase 4 — API documentation (ground truth for everything)

> **This is the most important phase. Everything else — RSD validation, query plan, pass/fail criteria — is derived from what the API docs say. The RSD is never consulted as a source of truth here. The vendor's own API documentation is the only ground truth for what the API actually supports.**

You now have the base API endpoint URL from Phase 3. The goal is to determine **every field that can be used as a server-side filter** on that endpoint, directly from the API's own documentation.

---

### 4a — Detect the API type and locate the machine-readable spec

Before searching for human-readable docs, first check whether a machine-readable spec exists. These are more reliable than prose docs because parameter names, types, and constraints are structured and unambiguous.

**Step 1 — Identify the API type from the Phase 3 baseline URL**

| Signal in the URL or log | API type |
|---|---|
| `Content-Type: application/json` in response headers | REST / JSON |
| `Content-Type: text/xml` or `application/soap+xml` | SOAP / XML |
| Request body is an XML envelope (`<soapenv:Envelope>`) | SOAP / XML |
| URL ends in `.asmx`, `.svc`, `?wsdl`, or `?WSDL` | SOAP / XML |
| Plain JSON response body, no envelope | REST / JSON |

If unsure, check the raw log for `Content-Type` headers or inspect the response body structure.

---

**Step 2 — Search for the machine-readable spec based on API type**

#### If REST / JSON → search for OpenAPI / Swagger spec

Run a web search:
```
<VendorName> OpenAPI spec
<VendorName> Swagger spec
<VendorName> swagger.json
<VendorName> openapi.yaml
```

Also check these common locations on the vendor's domain:
```
https://api.<vendor>.com/swagger.json
https://api.<vendor>.com/openapi.yaml
https://developer.<vendor>.com/openapi.json
https://<vendor>.com/api-docs
```

**If an OpenAPI / Swagger spec is found:**
- **Snippet-first rule:** Before fetching the spec file, run a targeted web search: `<VendorName> <endpoint-name> query parameters OpenAPI`. If the search snippet already lists the parameters with names and types — **stop here. Use the snippet.** Do not fetch the spec file.
- Only fetch the spec file if the snippet is truncated and the parameter list is incomplete.
- If you must fetch: extract only the `paths` block for the Phase 3 endpoint URL → the `parameters` array under the matching HTTP method, then discard the rest from context immediately. Never load the full spec.
- Each parameter entry gives you: `name`, `in` (path / query / header / cookie), `required`, `schema.type`, and `description`
- This is your authoritative filterable fields list — use it to build the table in §4c

Example OpenAPI parameter entry:
```yaml
parameters:
  - name: status
    in: query
    required: false
    schema:
      type: string
      enum: [active, inactive, pending]
    description: Filter by account status
```

This tells you: `WHERE Status = 'active'` should push as `?status=active`.

**If no spec is found**, fall through to §4b (human-readable docs).

---

#### If SOAP / XML → search for the WSDL

Run a web search:
```
<VendorName> WSDL
<VendorName> web service WSDL URL
<VendorName> <ServiceName> .wsdl
```

Also try appending `?wsdl` or `?WSDL` to the endpoint URL from Phase 3:
```
https://api.example.com/v1/Service.asmx?wsdl
```

**If a WSDL is found:**
- Fetch the WSDL file
- Under `<wsdl:portType>`, find the operation matching the table (e.g. `GetOrders`, `SearchCustomers`)
- Under `<wsdl:message>` for that operation's input, find the `<wsdl:part>` elements — these are the input fields
- Under `<wsdl:types>` → `<xs:schema>`, find the XSD type for the input message — this gives you field names, data types, and `minOccurs`/`maxOccurs` (required vs optional)
- Fields in the input message are your filter candidates

**Key difference from REST:** SOAP has no URL query params or path params. All filters go into the **XML request body** inside the `<soap:Body>` element. In Phase 5, you will check the outgoing POST body for the correct XML structure, not the URL.

Example WSDL-derived filter field:
```xml
<xs:element name="Status" type="xs:string" minOccurs="0"/>
```
This means `WHERE Status = 'active'` should appear in the POST body as `<Status>active</Status>` inside the request envelope.

**If no WSDL is found**, fall through to §4b (human-readable docs).

---

### 4b — Fall back to human-readable API docs

Use this step only if §4a found no OpenAPI spec or WSDL.

Using the endpoint URL captured in Phase 3 (e.g. `https://api.twilio.com/2010-04-01/Accounts`), find the corresponding page in the vendor's official API reference.

Search strategy:
```
<VendorName> API reference <resource_name> list endpoint parameters
```

Prefer the official developer portal over third-party aggregators.

**⚠️ Token-efficient fetch rule — always follow this:**
Do NOT fetch the full docs page. Search snippets are often enough. If you must fetch:
1. Run a targeted web search first: `<VendorName> <endpoint-name> query parameters list`
2. If the snippet already contains the parameter table with names and types — **stop there. Use the snippet.** Do not fetch the page.
3. Only fetch if the snippet is truncated and lacks param names. When you do fetch, immediately extract only the parameters table (name, in, type, required, description) and discard the rest of the page from context. Do not retain the full page HTML or prose.
4. Never re-fetch a page you already searched — use your search results.

If the table maps to multiple endpoints (e.g. a list endpoint `GET /resource` and a single-record endpoint `GET /resource/{id}`), document both — they may support different filter mechanisms.

---

### 4c — Classify every parameter the API accepts

Whether you sourced parameters from an OpenAPI spec, WSDL, or human-readable docs, classify every parameter the same way:

**For REST / JSON APIs:**

| Type | How it appears in the request | What it means for SQL pushdown |
|---|---|---|
| **Path parameter** | Embedded in the URL path: `GET /resource/{id}` | `WHERE id = 'x'` must rewrite the URL to `/resource/x` |
| **Query parameter** | Appended to URL: `GET /resource?status=active` | `WHERE status = 'active'` must append `?status=active` to the URL |
| **POST body field** | Sent in the JSON body of a POST request | `WHERE field = 'x'` must include `{"field": "x"}` in the body |

**For SOAP / XML APIs:**

| Type | How it appears in the request | What it means for SQL pushdown |
|---|---|---|
| **Input message field** | Inside `<soap:Body>` as an XML element | `WHERE Status = 'x'` must appear as `<Status>x</Status>` in the POST body |

Also note for each parameter:
- The **exact API-level name** (may differ from the JDBC column name — e.g. JDBC column `Name` might map to API param `FriendlyName`)
- **Supported operators** — most params only support `=`; some support range operators via separate params like `DateCreatedAfter` / `DateCreatedBefore`
- Whether the parameter is **optional or required**
- **Value format** — string, integer, ISO date, epoch ms, enum — format mismatches are a common pushdown bug

### 4d — Build the filterable fields table

Produce this table before writing any test queries. Note the spec source so the finding report is traceable. Share it with the user and confirm it looks right.

```
Table: <TableName>
API Type: REST/JSON  [or]  SOAP/XML
Spec source: OpenAPI — https://... / WSDL — https://... / Human docs — https://...
List endpoint:   GET  https://api.example.com/v1/<resource>
Single endpoint: GET  https://api.example.com/v1/<resource>/{Id}   (if exists)

Filterable fields (from API docs):
| JDBC Column  | API Param Name    | Filter Type    | Supported Operators | Notes                        |
|--------------|-------------------|----------------|---------------------|------------------------------|
| Sid          | {AccountSid}      | Path param     | = only              | Rewrites URL to /resource/x  |
| Status       | Status            | Query param    | = only              | ?Status=active               |
| Name         | FriendlyName      | Query param    | = only (exact)      | ?FriendlyName=test           |
| DateCreated  | DateCreated>      | Query param    | >= only             | ?DateCreated>=2024-01-01     |
```

If the API docs list no filterable parameters at all (pure read-only list with no filter support), document that explicitly — it means every WHERE clause the driver accepts for this table must be evaluated client-side, which is expected behavior and not a bug. Still run Phase 5 to confirm the driver is not incorrectly claiming server-side support.

---

## Phase 3d — RSD validation against API docs (all table types — always run after Phase 4)

**This phase always runs after Phase 4, for every table type. Never skip it.**

Now that you have the API docs ground truth from Phase 4, compare every RSD claim extracted in Phase 3c against what the API actually supports. The §4d filterable fields table is your reference — it is what is true. The RSD claims table is what someone said is true. Find every gap between them.

---

### Step 3d-1 — Cross-check endpoint

| RSD claim | API docs (§4d) | Result |
|---|---|---|
| URI endpoint path | Does the endpoint in §4d match the RSD URI? | ✓ MATCH or ✗ ENDPOINT BUG |
| HTTP method | Does the method in §4d match the RSD method? | ✓ MATCH or ✗ METHOD BUG |
| Path param `{Id}` in URI | Does §4d confirm a path param on this endpoint? | ✓ MATCH or ✗ PATH PARAM BUG |

---

### Step 3d-2 — Cross-check every filterable column claim

**For each column marked `xs:filterable="true"` in the RSD:**

| Check | Pass | Fail |
|---|---|---|
| Does the API actually accept this as a filter param? | §4d shows this param exists | No such param in §4d — RSD overclaims filterability |
| Is the `api:set` param name exactly correct? | Matches the API param name in §4d exactly (case-sensitive) | Name differs — param name mapping bug |
| Is the operator correct? | API supports the operator the RSD implies | API supports different operators — operator mapping bug |

**For each column NOT marked `xs:filterable` in the RSD (or attribute absent):**

| Check | Pass | Fail |
|---|---|---|
| Does the API support filtering on this field? | §4d shows no filter param for this field | §4d shows a filter param exists — RSD underclaims, missed pushdown |

---

### Step 3d-3 — Cross-check data types

For each column, compare RSD `xs:type` against the API type from the OpenAPI schema or WSDL XSD in §4d:

| RSD xs:type | API declared type | Verdict |
|---|---|---|
| string | string | MATCH |
| datetime | string (ISO 8601 format) | ACCEPTABLE |
| integer | string | TYPE MISMATCH |
| boolean | integer (0/1) | TYPE MISMATCH |
| string | enum (fixed values) | NOTE — consider adding enum values to RSD |

---

### Step 3d-4 — Cross-check include / multi-ID param

| RSD claim | API docs (§4d) | Result |
|---|---|---|
| RSD maps include param | §4d confirms multi-ID param exists | MATCH |
| RSD maps include param | §4d shows no such param | OVERCLAIM — RSD incorrectly claims include support |
| RSD has no include param | §4d shows multi-ID param exists | MISSED — driver will slice instead of using include param |
| RSD has no include param | §4d shows no multi-ID param | MATCH — slicing is correct behavior |

---

### Step 3d-5 — Produce the RSD validation report

Share this report with the user before generating the query plan. Do not silently skip any finding.

```
RSD Validation Report — <DriverName> — Table: <TableName>
APIP file:  <filename.apip>
RSD file:   <TableName.rsd>
API source: <OpenAPI URL / WSDL URL / docs URL>

── Endpoint ──────────────────────────────────────────────
RSD claims:    GET https://api.example.com/v1/<resource>/{Id}
API docs show: GET https://api.example.com/v1/<resource>/{Id}
Result: MATCH

── Filterable fields ─────────────────────────────────────
| Column    | RSD filterable | RSD api:set     | API param exists? | API param name  | Result            |
|-----------|---------------|-----------------|-------------------|-----------------|-------------------|
| Id        | true (path)   | {Id}            | Yes               | {id}            | MATCH             |
| Status    | true          | Status          | Yes               | status          | CASE MISMATCH     |
| Name      | true          | FriendlyName    | Yes               | friendly_name   | PARAM NAME BUG    |
| CreatedAt | false         | —               | Yes               | created_after   | MISSED PUSHDOWN   |
| UpdatedAt | true          | updated_at      | No                | —               | OVERCLAIM         |

── Data types ────────────────────────────────────────────
| Column    | RSD xs:type | API type      | Result          |
|-----------|-------------|---------------|-----------------|
| Id        | string      | string        | MATCH           |
| CreatedAt | datetime    | string (ISO)  | ACCEPTABLE      |
| Count     | string      | integer       | TYPE MISMATCH   |

── Include / multi-ID param ──────────────────────────────
API has multi-ID param: YES — ?ids=<id1>,<id2>
RSD maps include param: NO
Result: MISSED — driver will slice instead of using ?ids param

── Summary ───────────────────────────────────────────────
Overclaimed filterable (RSD says filterable, API disagrees):  UpdatedAt
Underclaimed filterable (API supports, RSD does not expose):  CreatedAt
Param name bugs:   Status (case mismatch), Name (FriendlyName vs friendly_name)
Type mismatches:   Count (string vs integer)
Missed include:    Id (API supports ?ids, RSD does not map it)
Endpoint:          MATCH
```

**What each finding means:**
- Overclaimed filterable → expect runtime FAIL in Phase 5 (driver pushes param but API ignores it or returns wrong results)
- Underclaimed filterable → missed optimization, not a runtime bug; file as improvement
- Param name bug → expect runtime FAIL in Phase 5 (driver sends wrong param name, API ignores filter)
- Type mismatch → driver may send wrongly-typed value; API may silently ignore or return 400
- Missed include param → driver will slice unnecessarily; file as performance improvement

**After sharing the report, proceed to Phase 4e.** The query plan is generated from the §4d API docs table — not from the RSD. Fields the RSD missed (underclaimed) are still in the query plan because the API supports them. Fields the RSD overclaimed are also still tested — Phase 5 will confirm whether the driver actually pushes them or not.

---

### 4e — Generate the full SQL test query plan

Before running any queries, convert every row in the **§4d API docs filterable fields table** into the complete set of SQL queries that exercise every server-side possibility the API documents for that field.

**Critical rule — query plan comes from API docs only, never from the RSD.**

The query plan is generated entirely from the §4d filterable fields table, which was built from the official API documentation. The RSD is used only for comparison in Phase 3d. A field missing from the RSD, or marked `filterable="false"` in the RSD, is still included in the query plan if the API docs say the field is a valid filter param.

Every field the API supports as a server-side filter must appear in the query plan and must be executed in Phase 5 — regardless of whether the RSD exposes it as a JDBC column or marks it as filterable. Specifically:

- **Fields in §4d that are also in the RSD and marked filterable** → generate queries normally, test pushdown
- **Fields in §4d that are in the RSD but NOT marked filterable (underclaimed)** → still generate queries and run them; this tests whether the driver silently supports the filter even though the RSD doesn't claim it
- **Fields in §4d that are NOT in the RSD at all (missing from driver)** → generate the query and attempt to run it; expected result is either a column-not-found error or client-side filtering — document which; this is a driver gap
- **Fields in the RSD marked filterable but NOT in §4d (overclaimed)** → still generate and run the query; expect the filter to be silently ignored by the API — runtime confirmation of the RSD overclaim

The only source that determines what goes into the query plan is §4d. The RSD is checked for comparison in Phase 3d but never gates what gets tested.

**Do not only test `WHERE field = 'value'`.** Every API-documented semantic gets its own query row.

---

#### Mapping API filter semantics to SQL queries

Use this lookup table. For every field in §4d, identify which rows apply (based on what the API docs say the parameter does), then generate the corresponding SQL.

| API filter semantic | Typical API param pattern | SQL query to generate |
|---|---|---|
| Exact match / equality | `?status=active` | `WHERE Status = 'active'` |
| Inclusion list (match any of) | `?status=active,inactive` or `?status[]=active&status[]=inactive` | `WHERE Status IN ('active', 'inactive')` |
| Exclusion list (exclude these values) | `?exclude_status=deleted` or `?status!=deleted` | `WHERE Status != 'deleted'` or `WHERE NOT Status = 'deleted'` |
| Range — lower bound (strict) | `?created_after=2024-01-01` or `?min_date=...` | `WHERE CreatedAt > '2024-01-01'` |
| Range — lower bound (inclusive) | `?created_after=2024-01-01` | `WHERE CreatedAt >= '2024-01-01'` |
| Range — upper bound (strict) | `?created_before=2024-12-31` or `?max_date=...` | `WHERE CreatedAt < '2024-12-31'` |
| Range — upper bound (inclusive) | `?created_before=2024-12-31` | `WHERE CreatedAt <= '2024-12-31'` |
| Range — both bounds (BETWEEN) | `?created_after=...&created_before=...` | `WHERE CreatedAt BETWEEN '2024-01-01' AND '2024-12-31'` |
| Boolean flag | `?is_active=true` | `WHERE IsActive = true` |
| Prefix / starts-with | `?name_prefix=foo` or `?q=foo*` | `WHERE Name LIKE 'foo%'` |
| Contains / substring search | `?q=foo` or `?search=foo` | `WHERE Name LIKE '%foo%'` |
| Null / not-null presence check | `?has_email=true` / `?email_exists=false` | `WHERE Email IS NOT NULL` / `WHERE Email IS NULL` |
| Path parameter (single resource) | `GET /resource/{id}` | `WHERE Id = '<real_id_from_baseline>'` |
| Combined multi-field filter | `?status=active&type=premium` | `WHERE Status = 'active' AND Type = 'premium'` |

---

#### Mandatory date/datetime filter matrix

**For every column with type `date`, `datetime`, or `timestamp` — always generate ALL of the following queries. This is unconditional: it does not matter whether the RSD marks the column as filterable, whether `other:supportedOperators` is set, or whether the API docs list date filter params for this column. All six operators must always be tested. Inspect the actual HTTP request for each.**

| # | SQL operator | SQL query to generate | What to check in the outgoing request |
|---|---|---|---|
| D1 | `=` (equality) | `WHERE <Col> = '<value>'` | Confirm which API param name is used (e.g. `createdAt=`) and the value format (ISO string vs epoch ms) |
| D2 | `>` (strict greater than) | `WHERE <Col> > '<value>'` | Should map to an "after" param (e.g. `createdAfter=`) — check param name |
| D3 | `>=` (greater than or equal) | `WHERE <Col> >= '<value>'` | Should also map to an "after" param — confirm whether `>` and `>=` produce the same or different params |
| D4 | `<` (strict less than) | `WHERE <Col> < '<value>'` | Should map to a "before" param (e.g. `createdBefore=`) |
| D5 | `<=` (less than or equal) | `WHERE <Col> <= '<value>'` | Should also map to a "before" param — confirm whether `<` and `<=` produce the same or different params |
| D6 | `BETWEEN` | `WHERE <Col> BETWEEN '<start>' AND '<end>'` | Should produce both an "after" and a "before" param in a single request |

For each operator, record:
- **API param name(s) used** — e.g. `createdAt`, `createdAfter`, `createdBefore`, `updated_min`, `date_gte`
- **Value format sent** — ISO 8601 string (`2024-01-01T00:00:00Z`), epoch milliseconds (`1704067200000`), epoch seconds, date-only (`2024-01-01`)
- **Server-side or client-side** — does the outgoing HTTP request change, or does the driver do a full fetch and filter in memory?

**Verdict rule — depends on whether the API supports date filtering for this column:**

- **If the API supports date filter params for this column** (confirmed from §4d):
  - Each operator that pushes a param to the API → **PASS**
  - `BETWEEN` not pushed at all (full-fetch + in-memory) → **FAIL**
  - Only one direction works (e.g. `>` pushes but `<` does not) → **PARTIAL**
  - Date value sent in wrong format (epoch ms when API expects ISO, or vice versa) → **FAIL**

- **If the API does NOT support date filter params for this column** (column is non-filterable, no date params in §4d):
  - All operators evaluated client-side (no date param appears in the URL for any D1–D6 query) → **PASS** (correct behavior — driver cannot push what the API does not accept)
  - Any date param appearing in the URL despite no API support → **FAIL** (driver overclaims pushdown)

**Common findings to watch for (when API does support date params):**
- `>` and `>=` map to the same API param (API treats them identically) — note it but not a bug
- `=` on a datetime pushes a range param instead of an exact-match param — note the semantics (exact millisecond match is rare in APIs)

Add a `D1`–`D6` row to the §4e test query plan for every date/datetime column. Do not collapse them into a single row.

**Key rule:** the API param name drives the mapping, not the JDBC column name. If the API docs say the param is called `exclude_labels` and accepts a comma-separated list, the test query is `WHERE Labels NOT IN ('x', 'y')` — and in Phase 5 you check the outgoing URL for `exclude_labels=x,y`.

---

#### Special rule — Path param + IN list: slicing vs include param

**This rule applies to every column whose value is embedded in the URL path — identified from the actual HTTP request in Phase 3 (the baseline GET URL), NOT from whether the RSD marks the column as `key=true` or `xs:filterable`. If the column's value appears as a path segment in the outgoing URL (e.g. `/v2/resource/<value>`), it is a path param and this rule applies unconditionally.**

This is a critical behavior to validate whenever a table has a **path parameter** field (e.g. `GET /resource/{Id}`).

**The problem:** SQL allows `WHERE Id IN ('a', 'b', 'c')` but a path param can only hold one value at a time (`/resource/a`). The driver must choose one of two strategies:

**Strategy 1 — Slicing (driver splits the IN list into multiple requests)**
The driver makes one API call per value: `GET /resource/a`, `GET /resource/b`, `GET /resource/c`, merges results, and returns them as a single JDBC resultset.

**Strategy 2 — Include param (API has a dedicated multi-value filter param)**
Some APIs offer a separate query param that accepts multiple IDs: `GET /resource?ids=a,b,c` or `GET /resource?include=a,b,c`. If this exists, the driver should use it instead of slicing — it is far more efficient.

**How to identify path param columns:**

Look at the baseline HTTP request URL captured in Phase 3. Any column whose value is embedded as a path segment (e.g. the URL is `GET /v2/resource/EFRNSHOEJ5FOLPVWRQTHSLPK` and the WHERE value `EFRNSHOEJ5FOLPVWRQTHSLPK` appears in the path) is a path param column. Do NOT rely on `xs:key="true"` or `other:supportedOperators` — those are RSD claims and may be wrong or missing. The actual URL is the source of truth.

**How to detect which strategy applies:**

Step 1 — Check the API docs for a multi-value ID param:
```
Search: <VendorName> API list endpoint multiple ids filter
Look for params named: ids, id, include, filter[id], id[in], id__in
```

Step 2 — Check the OpenAPI spec parameters for the list endpoint (`GET /resource`) — look for any param that accepts an array of IDs.

**If an include/multi-ID param exists → generate these queries:**

| # | SQL Query | Expected in outgoing request |
|---|---|---|
| PP-1 | `WHERE Id = '<real_id>'` | Single path: `GET /resource/<real_id>` |
| PP-2 | `WHERE Id IN ('<id1>', '<id2>')` | `GET /resource?ids=<id1>,<id2>` (not two separate calls) |
| PP-3 | `WHERE Id IN ('<id1>', '<id2>', '<id3>')` | `GET /resource?ids=<id1>,<id2>,<id3>` |

**If no include/multi-ID param exists → generate these slicing queries:**

| # | SQL Query | Expected in outgoing request |
|---|---|---|
| PP-1 | `WHERE Id = '<real_id>'` | Single path: `GET /resource/<real_id>` |
| PP-2 | `WHERE Id IN ('<id1>', '<id2>')` | Two separate calls: `GET /resource/<id1>` then `GET /resource/<id2>` |
| PP-3 | `WHERE Id IN ('<id1>', '<id2>', '<id3>')` | Three separate calls, results merged |

**Validation in Phase 5 for path param IN queries:**

For the slicing scenario — count the number of HTTP GET requests in the log after running the IN query. It must equal the number of values in the IN list.
```powershell
# Count GET requests made
(Get-Content "$env:TEMP\cdata_filter_test.log" | Where-Object { $_ -match "^\[HTTP" } | Where-Object { $_ -match "GET " }).Count
```
```bash
grep -c "GET " /tmp/cdata_filter_test.log
```

**Pass (slicing):** number of GET requests = number of values in IN list, and each URL contains one distinct ID value.
**Pass (include param):** single GET request with all IDs in a query param.
**FAIL:** single GET request to the list endpoint with no ID filter at all — driver fell back to fetching all records and filtering client-side.
**FAIL:** driver uses include param when the API doesn't support it — results in a 400 or empty response.

Add PP-1 through PP-3 rows to the §4e test query plan for **every column confirmed as a path param from the baseline URL** (regardless of `xs:key` or `xs:filterable`). Label each row `Path param IN — slicing` or `Path param IN — include param` in the Filter Semantic column so the expected behavior is unambiguous. Never skip this because the column lacks a `key=true` attribute.

---

#### Output: the test query plan

Produce this table and share it with the user before running anything. One row per query — not per field.

The `RSD Status` column records what the RSD says about this field — this is for traceability only and never gates whether the query is run. Every row runs regardless of RSD status.

```
Test Query Plan — <DriverName> — Table: <TableName>
Source: API docs (§4d) — <spec URL>

| # | JDBC Column   | API Param         | Filter Semantic       | SQL Query to Run                                                        | Expected in outgoing request              | RSD Status                  |
|---|---------------|-------------------|-----------------------|-------------------------------------------------------------------------|-------------------------------------------|-----------------------------|
| 1 | Status        | Status            | Equality              | SELECT * FROM T WHERE Status = 'active' LIMIT 5                        | ?Status=active                            | RSD: filterable (MATCH)     |
| 2 | Status        | Status            | Inclusion list        | SELECT * FROM T WHERE Status IN ('active','inactive') LIMIT 5          | ?Status=active,inactive                   | RSD: filterable (MATCH)     |
| 3 | CreatedAt     | created_after     | Range lower bound     | SELECT * FROM T WHERE CreatedAt >= '2024-01-01' LIMIT 5                | ?created_after=2024-01-01                 | RSD: NOT filterable (UNDERCLAIMED — test anyway) |
| 4 | UpdatedAt     | updated_at        | Equality              | SELECT * FROM T WHERE UpdatedAt = '2024-01-01' LIMIT 5                 | ?updated_at=2024-01-01                    | RSD: filterable but API has no such param (OVERCLAIMED) |
| 5 | Tags          | tags              | Equality              | SELECT * FROM T WHERE Tags = 'vip' LIMIT 5                             | ?tags=vip                                 | RSD: column missing entirely (DRIVER GAP) |
| 6 | Id            | {Id}              | Path param            | SELECT * FROM T WHERE Id = '<real_id>' LIMIT 5                         | URL rewrites to /resource/<real_id>       | RSD: filterable (MATCH)     |
| 7 | Id            | {Id}              | Path param IN slicing | SELECT * FROM T WHERE Id IN ('<id1>','<id2>') LIMIT 5                  | Two calls: /resource/<id1>, /resource/<id2> | RSD: filterable (MATCH)   |
```

**RSD Status values used in the column:**
- `RSD: filterable (MATCH)` — RSD and API docs agree this is filterable
- `RSD: NOT filterable (UNDERCLAIMED — test anyway)` — API supports it, RSD doesn't claim it; test to see if driver pushes it anyway
- `RSD: filterable but API has no such param (OVERCLAIMED)` — RSD claims filterable, API docs say no; test to confirm API ignores the filter
- `RSD: column missing entirely (DRIVER GAP)` — API supports the filter but this field isn't even exposed as a JDBC column; test will likely error or fall back to client-side; document the gap

Also include these standard edge-case rows for every field regardless of semantic:

| # | Filter Semantic       | SQL Query                                              | Expected behavior |
|---|----------------------|--------------------------------------------------------|-------------------|
| E1 | NULL check           | `WHERE <Field> IS NULL`                               | No crash; client-side if API doesn't support |
| E2 | Non-existent value   | `WHERE <Field> = '00000_does_not_exist'`              | 0 rows, no error |
| E3 | Empty string         | `WHERE <Field> = ''`                                  | 0 rows or API error handled gracefully |

Wait for the user to confirm the query plan before running Phase 5. If any row looks wrong (wrong test value, unsupported operator), correct it now.

---

### 4f — Do not infer from the RSD or driver behavior

- **Never use the RSD `xs:filterable` attribute as your source** — that attribute records what the driver *claims* to support. The entire point of this skill is to verify those claims against the real API.
- **Never use the Phase 3 log output as your filter reference** — the baseline `SELECT *` log shows what happens with no filters, not what the API is capable of accepting.
- If you cannot find official API documentation for the endpoint, note that explicitly and ask the user for a docs URL before continuing.

### 4g — Classifying undocumented params the driver sends

After live test runs, you may observe that the driver sends query params that are NOT listed in the official API docs, but the API still returns filtered results. Classify these as **undocumented — functional but at risk**:

- Do NOT mark them as PASS — the param is not guaranteed to remain supported.
- Do NOT mark them as FAIL — filtering does happen at the server.
- Mark them as **UNDOCUMENTED** in the results table and add a note: "Param `<name>` not listed in official API docs. Currently working in live test but may be removed without notice. File for vendor confirmation."
- These should also be flagged in the RSD validation report as a risk — the RSD should reference only documented API params.

---

## Phase 5 — Run filter queries and validate pushdown

Execute every query from the §4e test query plan in order. For each query: clear the log, run it, inspect the outgoing request.

**Clear the log before each test run** so you are reading only that query's HTTP traffic:

```powershell
# Windows
[System.IO.File]::Delete("$env:TEMP\cdata_filter_test.log")
java -cp ".;$jar" FilterPushdownTest "<query>" 2>&1
Get-Content "$env:TEMP\cdata_filter_test.log" | Where-Object { $_ -match "GET |POST " }
```

```bash
# Linux/Mac
rm -f /tmp/cdata_filter_test.log
java -cp ".:$JAR" FilterPushdownTest "<query>" 2>&1
grep -E "GET |POST " /tmp/cdata_filter_test.log
```

### 5a — Run each query from the test plan

For each row in the §4e query plan:
1. Substitute real test values — use actual data from the Phase 3 baseline (first row's ID, a known status value, a real date range)
2. Run the query exactly as written in the plan
3. Capture the outgoing HTTP request from the log
4. Compare it against the "Expected in outgoing request" column from the plan

For each test, note:
- The full outgoing HTTP URL or POST body — does it contain what the plan predicted?
- The number of HTTP requests made — 1 is ideal; >1 may indicate client-side paging through all records

**When checking the outgoing URL, use the API param name from the §4e plan — not the JDBC column name.**

**Special handling by RSD status:**

- **RSD: filterable (MATCH)** — run normally; expect pushdown; FAIL if filter not in outgoing request
- **RSD: NOT filterable (UNDERCLAIMED)** — run the query; if the JDBC column exists and the driver pushes the filter anyway, that is a hidden capability — mark as UNDERCLAIMED PASS (driver pushes but RSD doesn't claim it); if filter not pushed, mark as UNDERCLAIMED FAIL (driver missed an opportunity the API supports)
- **RSD: filterable but API has no such param (OVERCLAIMED)** — run the query; check whether the driver sends the param to the API; if it does, check whether the API uses it or ignores it (result count same as baseline = API ignored it); mark as OVERCLAIMED regardless
- **RSD: column missing entirely (DRIVER GAP)** — attempt the query; it will likely throw a column-not-found SQLException or return unfiltered results; either outcome confirms the driver gap; do not mark as FAIL — mark as GAP with the actual error documented

### 5b — Pushdown pass/fail criteria

**For REST / JSON APIs:**

| Field type | Expected behavior when pushed correctly |
|---|---|
| Path parameter (`id`) | URL changes from `/resource` to `/resource/<value>` |
| Query parameter (`status`) | URL contains `?status=<value>` or `&status=<value>` |
| POST body field (REST) | Request body JSON contains `{"field": "<value>"}` |

**For SOAP / XML APIs:**

| Field type | Expected behavior when pushed correctly |
|---|---|
| Input message field | POST body XML contains `<FieldName>value</FieldName>` inside `<soap:Body>` |

To extract the outgoing SOAP POST body from the Verbosity=5 log:

```powershell
# Windows — find lines containing the SOAP envelope or field values
Get-Content "$env:TEMP\cdata_filter_test.log" | Where-Object { $_ -match "soap|Body|Envelope|<\w+>" }
```

```bash
# Linux/Mac
grep -i "soap\|Body\|Envelope\|<[A-Za-z]" /tmp/cdata_filter_test.log
```

Look for the full `<soap:Envelope>...</soap:Envelope>` block. Confirm the filter field and value appear inside `<soap:Body>` as expected from the WSDL input message definition.

**Pushdown confirmed** = the filter value appears in the outgoing HTTP request (URL for REST, POST body for SOAP) exactly as the API expects it, AND the result count changes relative to the unfiltered baseline.

**Pushdown NOT happening** = the outgoing request is identical to the unfiltered baseline (no filter in URL or body), but the result set is smaller — meaning the driver fetched all records and filtered in memory.

### 5c — Edge case rows (E1–E3 from the query plan)

The §4e query plan already includes E1–E3 edge case rows for every field. Run them in the same way:

- **E1 NULL check** — `WHERE field IS NULL` — must not crash; if the API has no null-filter support, driver should handle client-side gracefully
- **E2 Non-existent value** — `WHERE field = '00000_does_not_exist'` — must return 0 rows, not an error
- **E3 Empty string** — `WHERE field = ''` — must return 0 rows or handle the API's response gracefully; must not throw an unhandled exception

---

## Phase 6 — Reporting findings

For each tested field, produce a finding using this format:

---

### Finding: `<TableName>.<FieldName>` — Filter Pushdown [PASS / FAIL / PARTIAL]

**Field:** `<FieldName>`  
**Table:** `<TableName>`  
**API param name:** `<api_param_name>` (source: vendor API docs)  
**Expected behavior:** Filter pushed as `<path param / query param / POST body field>`  
**Test query:**
```sql
SELECT * FROM <table> WHERE <FieldName> = '<test_value>';
```
**Outgoing HTTP request (actual):**
```
GET https://api.example.com/v1/endpoint[?params_seen]
```
**Result:** PASS — filter value `<test_value>` present in URL as expected.

*OR*

**Result:** FAIL — URL was `GET https://api.example.com/v1/endpoint` (no filter appended). Driver fetched all records client-side. Filter value not visible in any outgoing HTTP request.

**Severity:** High — for every record fetched when a path/query param filter was available, this is an N+1-style over-fetch.  
**Ticket reference:** *(fill in if raising a Jira ticket)*

---

Repeat one finding block per tested field.

At the end, produce a summary table. One row per query plan entry (not per field) — this way every semantic variant is individually accounted for:

```
Filter Pushdown Summary — <DriverName> — Table: <TableName>
Source: <vendor API docs URL>

| Plan # | JDBC Column | API Param      | Filter Semantic    | SQL Query (abbreviated)               | Expected in request       | Actual in request? | RSD Status          | Result               |
|--------|-------------|----------------|--------------------|---------------------------------------|---------------------------|--------------------|---------------------|----------------------|
| 1      | Status      | Status         | Equality           | WHERE Status = 'active'               | ?Status=active            | Yes                | MATCH               | PASS                 |
| 2      | Status      | Status         | Inclusion list     | WHERE Status IN ('active','inactive') | ?Status=active,inactive   | No                 | MATCH               | FAIL                 |
| 3      | CreatedAt   | created_after  | Range lower bound  | WHERE CreatedAt >= '2024-01-01'       | ?created_after=2024-01-01 | Yes                | UNDERCLAIMED        | UNDERCLAIMED PASS    |
| 4      | UpdatedAt   | updated_at     | Equality           | WHERE UpdatedAt = '2024-01-01'        | ?updated_at=2024-01-01    | Yes (API ignored)  | OVERCLAIMED         | OVERCLAIMED          |
| 5      | Tags        | tags           | Equality           | WHERE Tags = 'vip'                    | ?tags=vip                 | SQLException       | DRIVER GAP          | GAP                  |
| 6      | Id          | {Id}           | Path param         | WHERE Id = 'abc123'                   | /resource/abc123          | Yes                | MATCH               | PASS                 |
| E1     | Status      | —              | NULL check         | WHERE Status IS NULL                  | n/a (client-side)         | —                  | —                   | PASS                 |
```

**Result definitions:**
- PASS — filter pushed correctly to a documented API param; result count changed vs baseline
- FAIL — filter not pushed; driver fetched all records client-side
- PARTIAL — some semantics pushed (e.g. equality works) but others don't (e.g. `>` works but `<` does not, or `BETWEEN` falls back to client-side)
- UNDERCLAIMED PASS — RSD doesn't claim this field as filterable, but driver pushes it anyway; hidden capability, file as RSD improvement
- UNDERCLAIMED FAIL — RSD doesn't claim this field as filterable and driver doesn't push it either; API supports it but driver misses it entirely; file as missing pushdown bug
- OVERCLAIMED — RSD claims filterable but API has no such param; driver sends it, API ignores it; file as RSD overclaim bug
- UNDOCUMENTED — driver pushes a param not listed in official API docs; filtering works in live test but param is undocumented and may be removed; file for vendor confirmation
- GAP — field not exposed as JDBC column at all; query errored or returned unfiltered results; file as missing column bug

---

## Appendix A — Reading the driver log efficiently

The log at `Verbosity=5` is verbose. Use these patterns to find what matters:

**Windows PowerShell:**
```powershell
$log = "$env:TEMP\cdata_filter_test.log"

# All outgoing HTTP requests
Get-Content $log | Where-Object { $_ -match "GET |POST |PUT |PATCH |DELETE " }

# All unique URLs
(Get-Content $log | Select-String -Pattern "https?://\S+" -AllMatches).Matches.Value | Sort-Object -Unique
```

**Linux/Mac (bash):**
```bash
log=/tmp/cdata_filter_test.log

# All outgoing HTTP requests
grep -E "GET |POST |PUT |PATCH |DELETE " "$log"

# All unique URLs
grep -oP "https?://[^\s]+" "$log" | sort -u

# Filter-related debug lines
grep -i "filter\|pushdown\|where\|param" "$log"
```

Compare the URL from the unfiltered `SELECT *` run to the URL from each filtered run side-by-side to immediately see whether the filter changed the outgoing request.

---

## Appendix B — When no log is available (Fiddler/Burp alternative)

If log-based validation is not possible (e.g. HTTPS tunneling through a corporate proxy is stripping certs), use Fiddler or Burp Suite as a proxy:

1. Start Fiddler/Burp and enable HTTPS decryption
2. Set the JVM proxy: add to the Java run command:
   ```
   -Djava.net.useSystemProxies=true
   ```
   Or explicitly:
   ```
   -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=8888
   ```
3. Run the test queries
4. In Fiddler/Burp, filter sessions to the vendor's API host
5. Compare the URL/body across the unfiltered vs filtered runs — same principle as log analysis

---

## Appendix C — Common failure patterns and their meaning

| Symptom | What it means |
|---|---|
| Outgoing URL identical for filtered and unfiltered query | Pushdown not happening — client-side filtering only |
| `IN ('a','b')` pushes as `?field=a` only (first value) | Driver is not serializing the full inclusion list — partial pushdown bug |
| `IN ('a','b')` pushes as `?field=a&field=b` but API expects `?field=a,b` (comma-separated) | Driver is using repeated params instead of comma-separated format — RSD serialization bug |
| `!= 'x'` or `NOT IN` query pushes nothing to the URL | Driver is not translating SQL exclusion operators to the API's `exclude_*` param — client-side only |
| Range query (`>=`, `<=`) pushes only one bound to the URL | Driver handles one direction but not the other — partial pushdown; file separate bugs for lower and upper bound |
| `LIKE '%foo%'` or `LIKE 'foo%'` pushes nothing | Driver is not mapping LIKE patterns to the API's search/prefix params — client-side filtering only |
| API call made with no filter, but result count matches expected | Driver may be doing post-fetch filtering — verify by counting API response records vs JDBC result rows |
| Error like `400 Bad Request` on filtered query, not on `SELECT *` | Driver is pushing the filter incorrectly (wrong param name, wrong format, wrong operator) |
| `N` API calls for `N` rows when filtering by ID | Driver is not using the single-resource endpoint (e.g. `GET /resource/{id}`) and instead looping through list endpoint — major performance bug |
| Filter pushed but wrong field name used | RSD `api:set` maps to wrong API param name — file a bug against the RSD |
| Filter pushed but wrong value format | e.g. date sent as `YYYY-MM-DD` when API expects epoch ms — RSD formatter bug |
| URL shows JDBC column name instead of API key (e.g. `created_at=` instead of `createdAt=`) | Driver is not applying the `api:set` mapping — for dynamic tables, the profile's request construction is not translating the column name to the API param name |
| Filter value present in URL but API returns unfiltered results | Column name in URL doesn't match what the API expects — cross-check the Phase 3b mapping table against the vendor API docs |
| **SOAP:** POST body has no `<FieldName>` element for the filtered column | Driver is not writing the filter into the SOAP envelope — pushdown not happening for this SOAP input field |
| **SOAP:** POST body contains the field but with wrong element name | WSDL input message uses a different element name than what the driver is sending — RSD `api:set` mapping bug |
| **SOAP:** POST body contains the field but API returns a fault | Value format doesn't match XSD type in the WSDL (e.g. driver sends a string where the WSDL expects `xs:dateTime`) |
| **Path param IN — slicing:** single GET to list endpoint, no ID in URL | Driver fell back to client-side filtering instead of slicing — major over-fetch bug |
| **Path param IN — slicing:** correct number of calls but same URL repeated | Driver is not substituting each ID value into the path — all calls hit the same resource |
| **Path param IN — include param:** driver slices when API has `?ids=` param | RSD does not map the include param — file as performance bug; RSD needs `api:set` for the ids param |
| **Path param IN — include param:** driver uses `?ids=` but API returns 400 | RSD incorrectly maps include param that doesn't exist in this API version — RSD overclaim bug |
| **RSD overclaim:** column marked `xs:filterable="true"` but filter never reaches API | API docs show no such param — RSD incorrectly flags this field as filterable; driver pushes to URL but API ignores it |
| **RSD underclaim:** API has a filter param but RSD column has no `xs:filterable` | Driver does client-side filtering for a field that could be pushed — file as missed optimization |
| **RSD param name bug:** driver pushes filter but API returns empty or 400 | `api:set` in RSD maps to wrong param name — e.g. RSD sends `Status` but API expects `status` (case mismatch) or `filter[status]` |
| **RSD type mismatch:** driver sends string for an integer field | `xs:type` in RSD is wrong — API rejects the value or silently ignores it |
| **RSD endpoint bug:** driver hits wrong URL | URI in RSD `api:set` doesn't match the actual API endpoint — all queries hit wrong resource |
| **Undocumented param works in live test** | Param not listed in official API docs but API returns filtered results — classify as UNDOCUMENTED, not PASS; flag for vendor confirmation; do not treat as reliable pushdown |
| **Date column: `BETWEEN` not pushed** | Driver sends an unfiltered list request and evaluates both bounds in memory — PARTIAL or FAIL; file as missing BETWEEN pushdown |
| **Date column: only one direction pushed** | `>` / `>=` works (createdAfter) but `<` / `<=` does not, or vice versa — PARTIAL; file separate bugs for each missing direction |
| **Date column: wrong value format** | Driver sends epoch ms when API expects ISO 8601 string, or vice versa — API may return empty results or silently ignore the filter; check value format in the outgoing URL against the API docs |
| **Date column: `>` and `>=` produce different param names** | Some APIs have separate `after` (exclusive) and `afterOrEqual` (inclusive) params — confirm which the driver uses for each SQL operator |
| `ClassNotFoundException` on startup | Driver class name is wrong — re-run `jar tf <driver>.jar \| grep -i Driver.class` and use that exact name |
| Log file not created after run | The process lacks write access to the log path — use `System.getenv("TEMP")` on Windows or `/tmp` on Linux/Mac instead of a hardcoded root path |
