---
name: db-shared-setup
description: Shared setup for all CData DB driver QA operations. Always read this before any op-*.md file.
---

# Shared Setup — CData DB Driver

Run this once before any operation. All variables defined here are reused by op-*.md files.

---

## Phase 0 — Gather inputs

Ask for **all** of the following:

| Input | Notes |
|-------|-------|
| **JAR folder path** | Must contain exactly one `.jar` and one `.lic` |
| **Connection string** | Full `jdbc:cdata:<driver>://<host>;Database=<db>;User=<u>;Password=<p>` — **without** QueryPassThrough (added by each mode) |
| **Database/schema name** | Used to scope CREATE TABLE and queries |
| **DB engine type** | SQL Server / MySQL / PostgreSQL / Oracle / SQLite / other — affects syntax choices |

---

## Phase 1 — Verify JAR + LIC

```powershell
$jarFolder = "<jar_folder>"
$jar       = (Get-ChildItem $jarFolder -Filter "cdata.jdbc.*.jar" | Select-Object -First 1).FullName
$lic       = Get-ChildItem $jarFolder -Filter "*.lic" | Select-Object -First 1

if (-not $jar)  { Write-Host "STOP: No CData JDBC jar found in $jarFolder"; return }
if (-not $lic)  { Write-Host "STOP: No .lic file found in $jarFolder — driver will fail"; return }

Write-Host "JAR: $jar"
Write-Host "LIC: $($lic.FullName)"

$DC = (jar tf $jar | Select-String "Driver\.class" | Select-Object -First 1) -replace "/","." -replace "\.class",""
if (-not $DC) { Write-Host "STOP: Driver class not found in JAR"; return }
Write-Host "Driver class: $DC"
```

---

## Phase 2 — Compile the DB harness

This harness handles DDL (CREATE/DROP), DML (INSERT/UPDATE/DELETE), and DQL (SELECT).
It also prints the SQL sent to the server when Verbosity=5 is active.

```powershell
$sp = "$env:TEMP\cdata_db_$(Get-Random)"
New-Item -ItemType Directory $sp -Force | Out-Null

$dbCode = @"
import java.sql.*;
public class DBProbe {
    public static void main(String[] args) throws Exception {
        Class.forName(args[0]);
        String url = args[1];
        String sql = args[2];

        try (Connection c = DriverManager.getConnection(url)) {
            System.out.println("[CONNECTED]");
            try (Statement s = c.createStatement()) {
                String upper = sql.trim().toUpperCase();

                if (upper.startsWith("SELECT") || upper.startsWith("SHOW") || upper.startsWith("DESCRIBE")) {
                    try (ResultSet r = s.executeQuery(sql)) {
                        ResultSetMetaData m = r.getMetaData();
                        int n = m.getColumnCount();
                        StringBuilder h = new StringBuilder("[HDR]");
                        for (int i=1;i<=n;i++)
                            h.append("\t").append(m.getColumnName(i))
                             .append(":").append(m.getColumnTypeName(i))
                             .append("(").append(m.getPrecision(i)).append(")");
                        System.out.println(h);
                        int rows = 0;
                        while (r.next() && rows < 100) {
                            StringBuilder row = new StringBuilder("[ROW]");
                            for (int i=1;i<=n;i++)
                                row.append("\t").append(r.getString(i)==null?"__NULL__":r.getString(i));
                            System.out.println(row);
                            rows++;
                        }
                        System.out.println("[ROWCOUNT] " + rows);
                    }
                } else {
                    int affected = s.executeUpdate(sql);
                    System.out.println("[AFFECTED] " + affected);
                }
                System.out.println("[OK]");
            } catch (SQLException e) {
                System.out.println("[SQL_ERR] " + e.getMessage());
            }
        } catch (Exception e) {
            System.out.println("[CONN_ERR] " + e.getMessage());
        }
    }
}
"@
[System.IO.File]::WriteAllText("$sp\DBProbe.java", $dbCode, [System.Text.Encoding]::ASCII)
javac -cp $jar "$sp\DBProbe.java" -d $sp
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: Compilation failed"; return }
Write-Host "Harness compiled."
```

---

## Phase 3 — Shared helpers

```powershell
$baseConnStr = "<connection_string_without_qpt>"   # no trailing semicolon
$dbEngine    = "<SQL Server|MySQL|PostgreSQL|Oracle|SQLite>"
$schema      = "<DatabaseOrSchemaName>"
$testTable   = "CDataQATest_$(Get-Random -Max 9999)"   # unique per run to avoid conflicts

$probe_n = 0
function Run-DB([string]$tag, [string]$sql, [string]$qptMode = "True") {
    $script:probe_n++
    $logPath = "$sp\${tag}_$($script:probe_n).log"
    $url     = $baseConnStr + ";QueryPassThrough=$qptMode;Verbosity=5;LogFile=$logPath;"
    $out     = java -cp "$sp;$jar" DBProbe $DC $url $sql 2>&1
    Start-Sleep -Milliseconds 300
    $log     = Read-DBLog $logPath
    $parsed  = Parse-DBRows ($out -join "`n")
    return @{ Tag=$tag; SQL=$sql; Out=($out -join "`n"); Log=$log; Parsed=$parsed; LogPath=$logPath }
}

function Read-DBLog([string]$path) {
    $L = if(Test-Path $path){ Get-Content $path -EA SilentlyContinue }else{ @() }
    # For DB drivers, look for the SQL forwarded to the server
    $sentSQL   = @($L | Where-Object { $_ -match "Executing SQL|Sending query|Query:|SQL:" } | Select-Object -First 5)
    $received  = @($L | Where-Object { $_ -match "Received|Response|Result" } | Select-Object -First 5)
    $errors    = @($L | Where-Object { $_ -match "exception|error|SQLException" -and $_ -notmatch "^#" } | Select-Object -First 5)
    $rewritten = @($L | Where-Object { $_ -match "Rewritten|Translated|Rewrote" } | Select-Object -First 5)
    return [PSCustomObject]@{
        SentSQL    = $sentSQL
        Received   = $received
        Errors     = $errors
        Rewritten  = $rewritten
        HasError   = $errors.Count -gt 0
        Raw        = $L
    }
}

function Parse-DBRows([string]$out) {
    $lines = $out -split "`n"
    $hdrLine = $lines | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1
    $colDefs = ($hdrLine -replace "^\[HDR\]\t","" -split "\t")
    $cols    = $colDefs | ForEach-Object { ($_ -split ":")[0] }
    $types   = $colDefs | ForEach-Object { if($_ -match ":(.+)\("){$Matches[1]}else{""} }
    $data    = $lines | Where-Object { $_ -match "^\[ROW\]" } | ForEach-Object {
        $vals = ($_ -replace "^\[ROW\]\t","") -split "\t"
        $h = @{}
        for($i=0;$i -lt $cols.Count;$i++){ $h[$cols[$i]] = if($vals[$i] -eq "__NULL__"){$null}else{$vals[$i]} }
        $h
    }
    $count = if($out -match "\[ROWCOUNT\] (\d+)"){ [int]$Matches[1] }else{ $data.Count }
    return @{ Cols=$cols; Types=$types; Rows=@($data); Count=$count }
}

function Add-Result([string]$id,[string]$desc,[string]$verdict,[string]$note,[string]$bug="") {
    if(-not $script:results){ $script:results = [System.Collections.Generic.List[PSObject]]::new() }
    $r = [PSCustomObject]@{ ID=$id; Description=$desc; Verdict=$verdict; Note=$note; Bug=$bug }
    $script:results.Add($r)
    $flag = switch($verdict){ "PASS"{"✓"} "FAIL"{"✗"} "BUG"{"⚠"} "SKIPPED"{"⊘"} default{"ℹ"} }
    Write-Host "$flag $id | $verdict | $note"
}

# Returns the first element of $cols that case-insensitively matches any of $candidates.
# Fallback to first candidate so failures surface as SQL errors, not silent nulls.
function Find-Col([string[]]$cols, [string[]]$candidates) {
    foreach ($c in $candidates) {
        $m = $cols | Where-Object { $_ -ieq $c } | Select-Object -First 1
        if ($m) { return $m }
    }
    return $candidates[0]
}

# Remaps a raw sys_tablecolumns row (keyed by actual driver column names) to
# canonical names so all downstream code can use fixed keys regardless of driver.
function Normalize-TCRow($row, $colMap) {
    $h = @{}
    foreach ($canon in $colMap.Keys) { $h[$canon] = $row[$colMap[$canon]] }
    return $h
}

# DB-engine specific syntax helpers
function Get-CreateTypeSyntax([string]$engine) {
    switch($engine) {
        "SQL Server"   { return @{
            INT="INT"; BIGINT="BIGINT"; SMALLINT="SMALLINT"; TINYINT="TINYINT"
            DECIMAL="DECIMAL(10,2)"; FLOAT="FLOAT"; REAL="REAL"
            BIT="BIT"; CHAR="CHAR(10)"; VARCHAR="VARCHAR(255)"; NVARCHAR="NVARCHAR(255)"
            TEXT="TEXT"; NTEXT="NTEXT"; BINARY="BINARY(10)"; VARBINARY="VARBINARY(255)"
            DATE="DATE"; TIME="TIME"; DATETIME="DATETIME"; DATETIME2="DATETIME2"
            DATETIMEOFFSET="DATETIMEOFFSET"; SMALLDATETIME="SMALLDATETIME"
            UNIQUEIDENTIFIER="UNIQUEIDENTIFIER"; XML="XML"; JSON="NVARCHAR(MAX)"
        }}
        "MySQL"        { return @{
            INT="INT"; BIGINT="BIGINT"; SMALLINT="SMALLINT"; TINYINT="TINYINT"
            DECIMAL="DECIMAL(10,2)"; FLOAT="FLOAT"; DOUBLE="DOUBLE"
            BOOL="BOOLEAN"; CHAR="CHAR(10)"; VARCHAR="VARCHAR(255)"
            TEXT="TEXT"; MEDIUMTEXT="MEDIUMTEXT"; LONGTEXT="LONGTEXT"
            BLOB="BLOB"; MEDIUMBLOB="MEDIUMBLOB"
            DATE="DATE"; TIME="TIME"; DATETIME="DATETIME"; TIMESTAMP="TIMESTAMP"
            YEAR="YEAR"; ENUM="ENUM('A','B','C')"; JSON="JSON"
        }}
        "PostgreSQL"   { return @{
            INT="INTEGER"; BIGINT="BIGINT"; SMALLINT="SMALLINT"
            DECIMAL="NUMERIC(10,2)"; FLOAT="DOUBLE PRECISION"; REAL="REAL"
            BOOL="BOOLEAN"; CHAR="CHAR(10)"; VARCHAR="VARCHAR(255)"; TEXT="TEXT"
            BYTEA="BYTEA"; DATE="DATE"; TIME="TIME"; TIMESTAMP="TIMESTAMP"
            TIMESTAMPTZ="TIMESTAMPTZ"; INTERVAL="INTERVAL"; UUID="UUID"; JSON="JSON"; JSONB="JSONB"
            ARRAY="INTEGER[]"
        }}
        "Oracle"       { return @{
            NUMBER="NUMBER(10)"; INT="INTEGER"; FLOAT="FLOAT"
            CHAR="CHAR(10)"; VARCHAR2="VARCHAR2(255)"; NVARCHAR2="NVARCHAR2(255)"
            CLOB="CLOB"; BLOB="BLOB"
            DATE="DATE"; TIMESTAMP="TIMESTAMP"; TIMESTAMPTZ="TIMESTAMP WITH TIME ZONE"
        }}
        default        { return @{
            INT="INTEGER"; TEXT="TEXT"; REAL="REAL"; BLOB="BLOB"
        }}
    }
}

$typeMap = Get-CreateTypeSyntax $dbEngine
Write-Host "Setup complete. Test table will be: $testTable"
Write-Host "Type map loaded for $dbEngine ($(($typeMap.Keys).Count) types)"

# --- Discover actual sys_tablecolumns column names ---
# Column names vary across drivers. Always query LIMIT 1 first to read the HDR,
# then build $tcMap and use it for every sys_tablecolumns query in this session.
$rDiscTC = Run-DB "discover_tc" "SELECT * FROM sys_tablecolumns LIMIT 1" "True"
$tcRaw   = $rDiscTC.Parsed.Cols
$tcMap = @{
    "ColumnName"   = Find-Col $tcRaw @("ColumnName","Name","Column")
    "DataType"     = Find-Col $tcRaw @("DataType","DataTypeName","Type","SqlType")
    "IsNullable"   = Find-Col $tcRaw @("IsNullable","Nullable","AllowNull")
    "IsReadOnly"   = Find-Col $tcRaw @("IsReadOnly","ReadOnly")
    "IsInsertable" = Find-Col $tcRaw @("IsInsertable","Insertable")
    "IsUpdatable"  = Find-Col $tcRaw @("IsUpdatable","Updatable","IsUpdateable")
    "IsKey"        = Find-Col $tcRaw @("IsKey","Key","IsPrimaryKey","PrimaryKey")
    "ColumnSize"   = Find-Col $tcRaw @("ColumnSize","Size","Length","MaxLength")
    "TableName"    = Find-Col $tcRaw @("TableName","Table","TableId")
}
Write-Host "sys_tablecolumns — ColName='$($tcMap.ColumnName)' DataType='$($tcMap.DataType)' IsKey='$($tcMap.IsKey)' TableFilter='$($tcMap.TableName)'"
```

---

## Cleanup helper (call at end of run)

```powershell
function Cleanup-TestTable {
    Write-Host "`n=== CLEANUP: DROP TEST TABLE ==="
    $r = Run-DB "cleanup_drop" "DROP TABLE IF EXISTS $testTable" $script:currentQPT
    if($r.Out -match "\[OK\]"){ Write-Host "✓ Test table $testTable dropped." }
    else { Write-Host "⚠ Could not drop $testTable — clean up manually if needed." }
}
```

---

## Token & cost tracking

See `workflows/token-tracker.md` for full details. Include these in every DB workflow setup.

```powershell
$USAGE_LOG = "$env:USERPROFILE\.cdata-qa\usage-log.json"
$PRICING   = @{
    "claude-sonnet-4-6" = @{ Input=3.00;  Output=15.00; CacheWrite=3.75;  CacheRead=0.30 }
    "claude-sonnet-5-5" = @{ Input=3.00;  Output=15.00; CacheWrite=3.75;  CacheRead=0.30 }
    "claude-opus-4-6"   = @{ Input=15.00; Output=75.00; CacheWrite=18.75; CacheRead=1.50 }
    "claude-opus-5-5"   = @{ Input=15.00; Output=75.00; CacheWrite=18.75; CacheRead=1.50 }
    "claude-haiku-4-5"  = @{ Input=0.80;  Output=4.00;  CacheWrite=1.00;  CacheRead=0.08 }
    "default"           = @{ Input=3.00;  Output=15.00; CacheWrite=3.75;  CacheRead=0.30 }
}

function Get-ActualTokens {
    param([string]$ConversationSummary, [string]$Model = "claude-sonnet-4-6")
    $body = @{
        model      = $Model
        max_tokens = 5
        messages   = @(@{ role="user"; content="QA session summary for token tracking: $ConversationSummary. Reply with only the word OK." })
    } | ConvertTo-Json -Depth 5 -Compress
    try {
        $r = Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" -Method POST `
            -Headers @{ "Content-Type"="application/json"; "anthropic-version"="2023-06-01" } `
            -Body $body -ErrorAction Stop
        return @{ InputTokens=$r.usage.input_tokens; OutputTokens=$r.usage.output_tokens
                  CacheWriteTokens=([int]$r.usage.cache_creation_input_tokens)
                  CacheReadTokens=([int]$r.usage.cache_read_input_tokens); Success=$true; Error="" }
    } catch {
        return @{ InputTokens=0; OutputTokens=0; CacheWriteTokens=0; CacheReadTokens=0; Success=$false; Error=$_.Exception.Message }
    }
}

function Record-Usage {
    param([string]$Command,[string]$Driver,[string]$Table,
          [string]$Model="claude-sonnet-4-6",[string]$SessionSummary="")
    if (-not $SessionSummary) {
        $SessionSummary = "Command=$Command Driver=$Driver Table=$Table Probes=$script:probe_n Results=$($script:results.Count)"
    }
    Write-Host "  Fetching actual token counts from API..."
    $usage = Get-ActualTokens -ConversationSummary $SessionSummary -Model $Model
    if (-not $usage.Success) { Write-Host "  WARNING: Token tracking unavailable — $($usage.Error)"; return }

    $dir = Split-Path $USAGE_LOG
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory $dir -Force | Out-Null }
    $rates = if ($PRICING[$Model]){$PRICING[$Model]}else{$PRICING["default"]}
    $ic=($usage.InputTokens/1e6)*$rates.Input; $oc=($usage.OutputTokens/1e6)*$rates.Output
    $cwc=($usage.CacheWriteTokens/1e6)*$rates.CacheWrite; $crc=($usage.CacheReadTokens/1e6)*$rates.CacheRead
    $tc=$ic+$oc+$cwc+$crc
    $rec=[PSCustomObject]@{ Timestamp=(Get-Date -F "yyyy-MM-dd HH:mm:ss"); Command=$Command; Driver=$Driver
        Table=$Table; Model=$Model; InputTokens=$usage.InputTokens; OutputTokens=$usage.OutputTokens
        CacheWriteTokens=$usage.CacheWriteTokens; CacheReadTokens=$usage.CacheReadTokens
        TotalTokens=($usage.InputTokens+$usage.OutputTokens+$usage.CacheWriteTokens+$usage.CacheReadTokens)
        InputCostUSD=[math]::Round($ic,6); OutputCostUSD=[math]::Round($oc,6)
        CacheCostUSD=[math]::Round($cwc+$crc,6); TotalCostUSD=[math]::Round($tc,6); Notes="actual" }
    $ex=if(Test-Path $USAGE_LOG){try{Get-Content $USAGE_LOG -Raw|ConvertFrom-Json}catch{@()}}else{@()}
    @($ex)+$rec | ConvertTo-Json -Depth 5 | Set-Content $USAGE_LOG -Encoding UTF8
    Write-Host ("  TOKEN (actual): $Command | In={0} Out={1} Total={2} | Cost=`${3}" -f `
        $usage.InputTokens,$usage.OutputTokens,$rec.TotalTokens,[math]::Round($tc,4))
}
```
---

## Destructive operation gate

Call `Confirm-Destructive` before any unbounded UPDATE/DELETE, DROP, or irreversible SP.

```powershell
function Confirm-Destructive([string]$description) {
    Write-Host "`n⚠️  DESTRUCTIVE OPERATION: $description"
    Write-Host "   This cannot be undone. Type 'yes' to proceed, anything else to skip:"
    $response = Read-Host
    if ($response.Trim().ToLower() -ne 'yes') {
        Add-Result "DESTRUCTIVE-GATE" "User did not confirm: $description" "SKIPPED" "User replied: '$response'"
        return $false
    }
    return $true
}
```

Use it like this — any workflow, any operation:
```powershell
if (Confirm-Destructive "DELETE all rows from $testTable without WHERE clause") {
    $r = Run-DB "del_nowhere" "DELETE FROM $testTable" $script:currentQPT
    ...
}
```
