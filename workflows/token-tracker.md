---
name: token-tracker
description: >
  INTERNAL MODULE — not a user command. The user-facing command is /usage.
  Tracks exact token usage by making a real Anthropic API call at the end of every
  workflow and reading the usage block from the response (input_tokens, output_tokens,
  cache_read_input_tokens, cache_creation_input_tokens). No estimates, no formulas.
  Writes to ~/.cdata-qa/usage-log.json. Works in PowerShell (all platforms).
  When /usage is run, Claude reads the JSON log and presents a formatted in-chat summary.
---

# Token & Cost Tracker — Internal Module

> `/token-tracker` is NOT a valid command. Tell the user: "Type `/usage` to see the report."

---

## How exact token tracking works

At the end of every workflow, the skill makes a real call to the Anthropic API
(`https://api.anthropic.com/v1/messages`). The API response always includes a `usage`
block with exact counts:

```json
{
  "usage": {
    "input_tokens": 9843,
    "output_tokens": 412,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 4200
  }
}
```

These are the actual numbers — no guessing, no character division, no probe formulas.

The API call sends the entire conversation summary as the message so that the response
reflects the real context size of this session.

---

## Canonical log path and pricing

```powershell
$USAGE_LOG = "$env:USERPROFILE\.cdata-qa\usage-log.json"

$PRICING = @{
    "claude-sonnet-4-6" = @{ Input = 3.00;  Output = 15.00; CacheWrite = 3.75;  CacheRead = 0.30 }
    "claude-sonnet-5-5" = @{ Input = 3.00;  Output = 15.00; CacheWrite = 3.75;  CacheRead = 0.30 }
    "claude-opus-4-6"   = @{ Input = 15.00; Output = 75.00; CacheWrite = 18.75; CacheRead = 1.50 }
    "claude-opus-5-5"   = @{ Input = 15.00; Output = 75.00; CacheWrite = 18.75; CacheRead = 1.50 }
    "claude-haiku-4-5"  = @{ Input = 0.80;  Output = 4.00;  CacheWrite = 1.00;  CacheRead = 0.08 }
    "default"           = @{ Input = 3.00;  Output = 15.00; CacheWrite = 3.75;  CacheRead = 0.30 }
}
```

---

## Get-ActualTokens — fetch real token counts from the API

This function sends a single lightweight API call. The `usage` block in the response
contains the exact token counts for that call. We use the conversation summary as the
message content so the input token count reflects the actual session context.

```powershell
function Get-ActualTokens {
    param(
        [string]$ConversationSummary,   # brief summary of what was done this session
        [string]$Model = "claude-sonnet-4-6"
    )

    $body = @{
        model      = $Model
        max_tokens = 5
        messages   = @(
            @{
                role    = "user"
                content = "QA session summary for token tracking: $ConversationSummary. Reply with only the word OK."
            }
        )
    } | ConvertTo-Json -Depth 5 -Compress

    try {
        $response = Invoke-RestMethod `
            -Uri     "https://api.anthropic.com/v1/messages" `
            -Method  POST `
            -Headers @{
                "Content-Type"      = "application/json"
                "anthropic-version" = "2023-06-01"
            } `
            -Body $body `
            -ErrorAction Stop

        return @{
            InputTokens      = $response.usage.input_tokens
            OutputTokens     = $response.usage.output_tokens
            CacheWriteTokens = if ($response.usage.cache_creation_input_tokens) { $response.usage.cache_creation_input_tokens } else { 0 }
            CacheReadTokens  = if ($response.usage.cache_read_input_tokens)     { $response.usage.cache_read_input_tokens }     else { 0 }
            Success          = $true
            Error            = ""
        }
    } catch {
        return @{
            InputTokens      = 0
            OutputTokens     = 0
            CacheWriteTokens = 0
            CacheReadTokens  = 0
            Success          = $false
            Error            = $_.Exception.Message
        }
    }
}
```

---

## Record-Usage — called at the END of every workflow

This function:
1. Calls `Get-ActualTokens` to fetch exact counts from the Anthropic API
2. Computes cost using the pricing table
3. Appends a structured record to `~/.cdata-qa/usage-log.json`
4. Prints a terminal summary box

```powershell
function Record-Usage {
    param(
        [string]$Command,                    # e.g. "/filter", "/qpttrue select", "/sp"
        [string]$Driver,                     # e.g. "Square JDBC"
        [string]$Table,                      # table name, SP name, or "n/a"
        [string]$Model = "claude-sonnet-4-6",
        [string]$SessionSummary = ""         # brief description passed to Get-ActualTokens
    )

    # Build a summary if not provided
    if (-not $SessionSummary) {
        $SessionSummary = "Command=$Command Driver=$Driver Table=$Table ProbeCount=$script:probe_n ResultCount=$($script:results.Count)"
    }

    # Get actual token counts from the API
    Write-Host "  Fetching actual token counts from API..."
    $usage = Get-ActualTokens -ConversationSummary $SessionSummary -Model $Model

    if (-not $usage.Success) {
        Write-Host "  WARNING: Could not fetch token counts — $($usage.Error)"
        Write-Host "  Token usage will NOT be recorded for this run."
        return
    }

    # Ensure log directory exists
    $dir = Split-Path $USAGE_LOG
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory $dir -Force | Out-Null }

    # Compute cost
    $rates = if ($PRICING[$Model]) { $PRICING[$Model] } else { $PRICING["default"] }
    $ic    = ($usage.InputTokens      / 1e6) * $rates.Input
    $oc    = ($usage.OutputTokens     / 1e6) * $rates.Output
    $cwc   = ($usage.CacheWriteTokens / 1e6) * $rates.CacheWrite
    $crc   = ($usage.CacheReadTokens  / 1e6) * $rates.CacheRead
    $total = $ic + $oc + $cwc + $crc

    # Build the record — JSON structure intentionally flat for easy Claude parsing
    $rec = [PSCustomObject]@{
        Timestamp        = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        Command          = $Command
        Driver           = $Driver
        Table            = $Table
        Model            = $Model
        InputTokens      = $usage.InputTokens
        OutputTokens     = $usage.OutputTokens
        CacheWriteTokens = $usage.CacheWriteTokens
        CacheReadTokens  = $usage.CacheReadTokens
        TotalTokens      = $usage.InputTokens + $usage.OutputTokens + $usage.CacheWriteTokens + $usage.CacheReadTokens
        InputCostUSD     = [math]::Round($ic,    6)
        OutputCostUSD    = [math]::Round($oc,    6)
        CacheCostUSD     = [math]::Round($cwc + $crc, 6)
        TotalCostUSD     = [math]::Round($total, 6)
        Notes            = "actual"
    }

    # Append to log (load existing, add new, write back)
    $existing = if (Test-Path $USAGE_LOG) {
        try { Get-Content $USAGE_LOG -Raw | ConvertFrom-Json } catch { @() }
    } else { @() }
    @($existing) + $rec | ConvertTo-Json -Depth 5 | Set-Content $USAGE_LOG -Encoding UTF8

    # Terminal summary box
    Write-Host ""
    Write-Host "  ┌─ TOKEN USAGE (actual) ─────────────────────────────────────────"
    Write-Host ("  │  Command : {0}" -f $Command)
    Write-Host ("  │  Driver  : {0}  |  Table: {1}" -f $Driver, $Table)
    Write-Host ("  │  Model   : {0}" -f $Model)
    Write-Host ("  │  Tokens  : Input={0}  Output={1}  CacheW={2}  CacheR={3}" -f `
        $usage.InputTokens, $usage.OutputTokens, $usage.CacheWriteTokens, $usage.CacheReadTokens)
    Write-Host ("  │  Total   : {0} tokens" -f $rec.TotalTokens)
    Write-Host ("  │  Cost    : Input=`${0}  Output=`${1}  Cache=`${2}  TOTAL=`${3}" -f `
        [math]::Round($ic,4), [math]::Round($oc,4), [math]::Round($cwc+$crc,4), [math]::Round($total,4))
    Write-Host ("  │  Saved   : {0}" -f $USAGE_LOG)
    Write-Host "  └────────────────────────────────────────────────────────────────"
}
```

---

## Show-UsageReport — triggered by /usage

**Dual output: PowerShell terminal box + in-chat Claude summary**

When `/usage` is invoked:
1. The PowerShell script reads `~/.cdata-qa/usage-log.json` and prints the terminal box.
2. The script then writes the JSON content to stdout in a `CLAUDE_USAGE_JSON:` prefixed line.
3. Claude reads that output, parses the JSON, and presents a formatted **in-chat markdown summary**.

### Step 1 — PowerShell reads the log and emits JSON for Claude

```powershell
function Show-UsageReport {
    if (-not (Test-Path $USAGE_LOG)) {
        Write-Host ""
        Write-Host "  No usage log at: $USAGE_LOG"
        Write-Host "  It is created automatically when any skill command finishes."
        # Emit sentinel so Claude knows there is no data
        Write-Output "CLAUDE_USAGE_JSON:null"
        return
    }

    $records = try {
        Get-Content $USAGE_LOG -Raw | ConvertFrom-Json
    } catch {
        Write-Host "  Error reading log: $_"
        Write-Output "CLAUDE_USAGE_JSON:null"
        return
    }

    if (-not $records -or @($records).Count -eq 0) {
        Write-Host "  Log exists but contains no records."
        Write-Output "CLAUDE_USAGE_JSON:null"
        return
    }
    $records = @($records)

    # ── Terminal box (unchanged) ──────────────────────────────────────────────
    Write-Host ""
    Write-Host "  ╔══ CDATA QA — TOKEN USAGE REPORT ════════════════════════════════╗"
    Write-Host ("  ║  Log  : {0}"           -f $USAGE_LOG)
    Write-Host ("  ║  Runs : {0}  (all actual counts from API)" -f $records.Count)
    Write-Host "  ╠══ BY COMMAND ════════════════════════════════════════════════════╣"
    $records | Group-Object Command | Sort-Object { ($_.Group | Measure-Object TotalCostUSD -Sum).Sum } -Descending | ForEach-Object {
        $g = $_.Group
        Write-Host ("  ║  {0,-26}  runs={1,-4} tokens={2,-10} cost=`${3}" -f `
            $_.Name, $g.Count,
            ($g | Measure-Object TotalTokens  -Sum).Sum,
            [math]::Round(($g | Measure-Object TotalCostUSD -Sum).Sum, 4))
    }
    Write-Host "  ╠══ BY MODEL ══════════════════════════════════════════════════════╣"
    $records | Group-Object Model | ForEach-Object {
        $g = $_.Group
        Write-Host ("  ║  {0,-32}  tokens={1,-10} cost=`${2}" -f `
            $_.Name,
            ($g | Measure-Object TotalTokens  -Sum).Sum,
            [math]::Round(($g | Measure-Object TotalCostUSD -Sum).Sum, 4))
    }
    Write-Host "  ╠══ TOTALS ════════════════════════════════════════════════════════╣"
    Write-Host ("  ║  Total runs   : {0}"   -f $records.Count)
    Write-Host ("  ║  Total tokens : {0}"   -f ($records | Measure-Object TotalTokens  -Sum).Sum)
    Write-Host ("  ║  Total cost   : `${0}" -f [math]::Round(($records | Measure-Object TotalCostUSD -Sum).Sum, 4))
    Write-Host "  ╠══ LAST 10 RUNS ══════════════════════════════════════════════════╣"
    $records | Select-Object -Last 10 | ForEach-Object {
        Write-Host ("  ║  {0}  {1,-24}  tok={2,-7}  `${3}" -f `
            $_.Timestamp, $_.Command, $_.TotalTokens, $_.TotalCostUSD)
    }
    Write-Host "  ╚══════════════════════════════════════════════════════════════════╝"

    # ── Emit JSON for Claude to parse ─────────────────────────────────────────
    # This line is read by Claude to build the in-chat summary.
    # Format: single line starting with CLAUDE_USAGE_JSON: followed by compact JSON array.
    $jsonPayload = $records | ConvertTo-Json -Depth 5 -Compress
    Write-Output "CLAUDE_USAGE_JSON:$jsonPayload"
}

Show-UsageReport
```

### Step 2 — Claude parses the output and presents an in-chat summary

After running the PowerShell script above, Claude must:

1. Find the line in the output that starts with `CLAUDE_USAGE_JSON:`.
2. Strip the prefix and parse the remainder as a JSON array.
3. If the value is `null` or the line is absent, respond:
   > "No usage log found yet. It will be created automatically when any workflow finishes."
4. If records exist, present the following **markdown summary in chat** (do not just echo the terminal box):

---

#### In-chat summary format Claude must render

```
## 📊 CData QA — Token Usage Report

**Total runs:** {N}  |  **Total tokens:** {T}  |  **Total cost:** ${C}

### By Command
| Command | Runs | Tokens | Cost (USD) |
|---------|------|--------|------------|
| /filter | 3    | 42,100 | $0.1263    |
| /auth   | 1    | 18,400 | $0.0552    |
| ...     | ...  | ...    | ...        |

### By Model
| Model | Tokens | Cost (USD) |
|-------|--------|------------|
| claude-sonnet-4-6 | 60,500 | $0.1815 |

### Last 10 Runs
| Timestamp           | Command          | Driver       | Tokens | Cost   |
|---------------------|-----------------|--------------|--------|--------|
| 2026-09-29 10:42:00 | /filter         | Square JDBC  | 14,200 | $0.043 |
| ...                 | ...             | ...          | ...    | ...    |

> 💾 Log file: `~/.cdata-qa/usage-log.json`
```

Populate the tables from the parsed JSON. Use `Intl`-style comma formatting for token
counts (`42,100` not `42100`). Round costs to 4 decimal places. Sort commands by cost
descending. Show the last 10 runs chronologically (oldest first among the 10).

**Do not** reproduce the terminal box in chat. The markdown table is the in-chat answer.

---

## How to call Record-Usage at the end of each workflow

Every workflow ends with this — just swap in the right Command, Driver, and Table values:

```powershell
# Called at the very end of every workflow, after the report is printed
Record-Usage `
    -Command        "/filter" `
    -Driver         "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table          $TABLE `
    -Model          "claude-sonnet-4-6" `
    -SessionSummary "Command=/filter Driver=$Driver Table=$TABLE Probes=$script:probe_n Results=$($script:results.Count)"
```

The `SessionSummary` string is sent to the API to measure the actual context. Keep it
factual and brief — it should describe what ran, not repeat all the test output.

---

## Auto-log guarantee

`Record-Usage` is the mechanism that guarantees every command is logged.
It is called:
- At the final step of every API driver workflow (`/filter`, `/auth`, `/cud`, `/sp`, `/general-testing`, `/perf`)
- At the final step of every DB driver workflow (all `/qpttrue` and `/qptfalse` operations) via the `db-shared-setup.md` shared `Record-Usage` definition

If a workflow exits early (user cancels, destructive gate skipped, fatal error), Claude
must still attempt `Record-Usage` with `Notes = "partial"` appended to the SessionSummary,
so partial runs are visible in `/usage` and not silently dropped.

To mark a partial run, append to the SessionSummary before calling:
```powershell
$SessionSummary += " [PARTIAL — reason: $reason]"
Record-Usage ... -SessionSummary $SessionSummary
```

---

## What if the API call fails?

If `Get-ActualTokens` returns `Success=$false`:
- Print a warning
- Do NOT write a record with zeroes (that would corrupt the averages)
- The workflow still completes normally — token tracking failure is non-fatal

Common causes: network restriction, missing API key in the environment, rate limit.
In those cases, tell the user: "Token tracking unavailable — API call failed: [error].
Run `/usage` later if tracking resumes."
