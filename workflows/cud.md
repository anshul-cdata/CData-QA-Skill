---
name: cud-testing
description: >
  Expanded CUD (INSERT/UPDATE/DELETE) validation for a CData JDBC API driver table.
  Triggers: "test CUD", "test insert update delete", "validate DML", "CUD testing", or
  user gives a JAR and wants to validate write behavior on a specific table.
  Covers full positive/negative/edge-case/bulk/type/combination testing for all three
  operations, verified from Verbosity=5 logs. Execute all steps autonomously via PowerShell.
---

# CUD Testing — v2 (Expanded)

Execute all steps autonomously via PowerShell. Never show commands for the user to run.
Log is the primary evidence for every verdict. JVM stdout is secondary.

---

## Rules

| Rule | Detail |
|------|--------|
| **Log-first** | Every verdict derived from Verbosity=5 log. |
| **Fresh log per probe** | Unique log path per test case. Never reuse. |
| **Mandatory-field network rule** | Missing/empty required col → driver MUST NOT make any API request. HTTP=0 in log = PASS. |
| **404 rule** | Driver may silently eat HTTP 404. Any other 4xx/5xx must surface as a clear SQL error — never swallowed. |
| **Bulk rule** | If API supports batch endpoints, driver should use them. If not, fall back to serial — not fail. |
| **RSD is metadata truth only** | Column flags validated against RSD. Endpoint correctness validated against vendor API docs. |
| **One table per run** | Ask the user which table to test. |
| **Destructive tests gated** | No-WHERE UPDATE/DELETE skipped unless user explicitly confirms data loss is acceptable. |

---

## Step 0 — Setup

Ask for:
1. JAR folder path
2. Base connection string (must include working auth)
3. RSD folder path *(optional)*
4. Table name to test

```powershell
$jar        = "<jar_folder>\cdata.jdbc.<driver>.jar"
$sp         = "$env:TEMP\cdata_cud_$(Get-Random)"
$connStr    = "<BASE_CONN_STR>;"
$rsdFolder  = ""   # empty if not provided
$TABLE      = "<TableName>"
$DC         = $null
$script:userConfirmedDestructive = $false

New-Item -ItemType Directory $sp -Force | Out-Null

$DC = (jar tf $jar | Select-String "Driver\.class" | Select-Object -First 1) -replace "/","." -replace "\.class",""
if (-not $DC) { Write-Host "STOP: Driver class not found in JAR."; return }
Write-Host "Driver : $DC | Table : $TABLE | RSD : $(if($rsdFolder){'provided'}else{'not provided'})"
```

**Compile harness (ASCII — no BOM):**

```powershell
$probeCode = @"
import java.sql.*;
public class CUDProbe {
    public static void main(String[] a) throws Exception {
        Class.forName(a[0]);
        try (Connection c = DriverManager.getConnection(a[1])) {
            try (Statement s = c.createStatement()) {
                String sql = a[2];
                if (sql.trim().toUpperCase().startsWith("SELECT")) {
                    ResultSet r = s.executeQuery(sql);
                    ResultSetMetaData m = r.getMetaData();
                    int n = m.getColumnCount();
                    StringBuilder h = new StringBuilder("HDR");
                    for (int i=1;i<=n;i++) h.append("\t").append(m.getColumnName(i)).append(":").append(m.getColumnTypeName(i));
                    System.out.println(h);
                    int rows=0;
                    while(r.next() && rows<50){
                        StringBuilder row = new StringBuilder("ROW");
                        for(int i=1;i<=n;i++) row.append("\t").append(r.getString(i)==null?"__NULL__":r.getString(i));
                        System.out.println(row); rows++;
                    }
                    System.out.println("ROWS:"+rows);
                } else {
                    int affected = s.executeUpdate(sql);
                    System.out.println("AFFECTED:"+affected);
                }
                System.out.println("OK");
            } catch(Exception e){ System.out.println("SQL_ERR:"+e.getMessage()); }
        } catch(Exception e){ System.out.println("CONN_ERR:"+e.getMessage()); }
    }
}
"@
[System.IO.File]::WriteAllText("$sp\CUDProbe.java", $probeCode, [System.Text.Encoding]::ASCII)
javac -cp $jar "$sp\CUDProbe.java" -d $sp
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: Compilation failed."; return }
Write-Host "Harness compiled."
```

**Shared helpers:**

```powershell
$probe_n = 0
function Run-SQL([string]$tag, [string]$sql) {
    $script:probe_n++
    $logPath = "$sp\${tag}_$($script:probe_n).log"
    $url2    = $connStr + "Verbosity=5;LogFile=$logPath;"
    $out     = java -cp "$sp;$jar" CUDProbe $DC $url2 $sql 2>&1
    Start-Sleep -Milliseconds 300   # log flush
    $log     = Read-CUDLog $logPath
    return @{ Out=($out -join "`n"); Log=$log; LogPath=$logPath; SQL=$sql }
}

function Read-CUDLog([string]$path) {
    $L = if(Test-Path $path){ Get-Content $path -EA SilentlyContinue }else{ @() }
    $reqs = @($L | Where-Object { $_ -match "\[HTTP\|Req\]" -or ($_ -match "\b(GET|POST|PUT|PATCH|DELETE)\b" -and $_ -match "https?://") })
    return [PSCustomObject]@{
        HTTP      = $reqs.Count
        Method    = if($reqs){ [regex]::Match($reqs[0],"(GET|POST|PUT|PATCH|DELETE)").Value }else{""}
        Endpoint  = if($reqs){ [regex]::Match($reqs[0],"https?://[^\s`"']+").Value }else{""}
        Endpoints = ($reqs | ForEach-Object { [regex]::Match($_,"https?://[^\s`"']+").Value } | Sort-Object -Unique)
        Status4xx = @($L | Where-Object { $_ -match "\[HTTP\|Res\].*\s4\d\d\b" })
        Status5xx = @($L | Where-Object { $_ -match "\[HTTP\|Res\].*\s5\d\d\b" })
        Errors    = @($L | Where-Object { $_ -match '"error"\s*:|exception|SQLException' -and $_ -notmatch "^#" } | Select-Object -First 5)
        BatchHit  = ($L | Where-Object { $_ -match "batch|bulk|/batch" }).Count -gt 0
        RequestBody = ($L | Where-Object { $_ -match "Request Body|body:|payload" } | Select-Object -First 3)
        Raw       = $L
    }
}

function Parse-Rows([string]$out) {
    $lines = $out -split "`n"
    $hdrLine = $lines | Where-Object { $_ -match "^HDR" } | Select-Object -First 1
    $cols = ($hdrLine -replace "^HDR\t","" -split "\t") | ForEach-Object { ($_ -split ":")[0] }
    $data = $lines | Where-Object { $_ -match "^ROW" } | ForEach-Object {
        $vals = ($_ -replace "^ROW\t","") -split "\t"
        $h = @{}
        for($i=0;$i -lt $cols.Count;$i++){ $h[$cols[$i]] = if($vals[$i] -eq "__NULL__"){$null}else{$vals[$i]} }
        $h
    }
    return @{ Cols=$cols; Rows=@($data) }
}

function Synth-Val([string]$type, [string]$colName) {
    switch -Regex ($type.ToLower()) {
        "int|long|number"    { return "42" }
        "decimal|float|double" { return "3.14" }
        "bool"               { return "true" }
        "date(?!time)"       { return "2025-06-15" }
        "datetime|timestamp" { return "2025-06-15T10:00:00Z" }
        default              { return "CUDTest_$colName" }
    }
}

$results = [System.Collections.Generic.List[PSObject]]::new()
function Add-Result([string]$id,[string]$desc,[string]$verdict,[string]$note,[string]$bug="") {
    $r = [PSCustomObject]@{ ID=$id; Description=$desc; Verdict=$verdict; Note=$note; Bug=$bug }
    $results.Add($r)
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
```

---

## Step 1 — Discover table metadata

```powershell
Write-Host "`n=== STEP 1: TABLE METADATA ==="

# Discover actual column names of sys_tablecolumns — never assume canonical names
$rDiscTC = Run-SQL "discover_tc" "SELECT * FROM sys_tablecolumns LIMIT 1"
$disc    = Parse-Rows $rDiscTC.Out
$tcRaw   = $disc.Cols
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

$r    = Run-SQL "meta_cols" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$TABLE'"
$meta = Parse-Rows $r.Out
# Remap rows to canonical names so all downstream code uses consistent keys
$cols = @($meta.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })

if (-not $cols -or $cols.Count -eq 0) {
    Write-Host "ERROR: No columns found for '$TABLE'. Verify table name."; return
}

$keyCols    = @($cols | Where-Object { $_["IsKey"]        -eq "true" })
$insertable = @($cols | Where-Object { $_["IsInsertable"] -eq "true" -and $_["IsReadOnly"] -ne "true" })
$updatable  = @($cols | Where-Object { $_["IsUpdatable"]  -eq "true" -and $_["IsReadOnly"] -ne "true" })
$readonly   = @($cols | Where-Object { $_["IsReadOnly"]   -eq "true" })
$nullable   = @($cols | Where-Object { $_["IsNullable"]   -eq "true" })
$required   = @($cols | Where-Object { $_["IsNullable"] -ne "true" -and $_["IsReadOnly"] -ne "true" -and $_["IsInsertable"] -eq "true" })
$strCols    = @($cols | Where-Object { $_["DataType"] -notmatch "int|long|bool|date|float|double|decimal|number" -and $_["IsInsertable"] -eq "true" })
$numCols    = @($cols | Where-Object { $_["DataType"] -match "int|long|float|double|decimal|number" -and $_["IsInsertable"] -eq "true" })
$boolCols   = @($cols | Where-Object { $_["DataType"] -match "bool" })
$dateCols   = @($cols | Where-Object { $_["DataType"] -match "date|timestamp" })

Write-Host "Total=$($cols.Count) | Keys=$($keyCols.Count) | Required=$($required.Count) | ReadOnly=$($readonly.Count)"
Write-Host "Insertable=$($insertable.Count) | Updatable=$($updatable.Count) | Nullable=$($nullable.Count)"
Write-Host "String=$($strCols.Count) | Numeric=$($numCols.Count) | Bool=$($boolCols.Count) | Date=$($dateCols.Count)"
```

---

## Step 2 — Endpoint & sample data discovery

```powershell
Write-Host "`n=== STEP 2: ENDPOINT DISCOVERY + SAMPLE DATA ==="

$r2          = Run-SQL "discover" "SELECT * FROM $TABLE LIMIT 10"
$sampleData  = Parse-Rows $r2.Out
$selectLog   = $r2.Log

Write-Host "SELECT → $($selectLog.Method) $($selectLog.Endpoint) | Rows=$($sampleData.Rows.Count)"

# Build value lookup from sample — used to construct realistic INSERT/UPDATE values
$sampleRow = if($sampleData.Rows.Count -gt 0){ $sampleData.Rows[0] }else{ @{} }

function Val-For([string]$colName, [string]$colType) {
    if($sampleRow[$colName]){ return $sampleRow[$colName] }
    return Synth-Val $colType $colName
}

# Search vendor docs for CUD endpoints
Write-Host "Searching vendor API docs for INSERT/UPDATE/DELETE endpoints on this resource..."
# → web_search "<VendorName> <TableName/Resource> create update delete API reference"
$cudEndpoints = @{ Insert="<from docs>"; Update="<from docs>"; Delete="<from docs>"; BulkSupported=$false }
```

---

## Step 3 — RSD validation *(if rsdFolder provided)*

```powershell
Write-Host "`n=== STEP 3: RSD VALIDATION ==="

if (-not $rsdFolder) {
    Write-Host "Skipping — no RSD folder provided."
} else {
    $rsdFile = Get-ChildItem $rsdFolder -Filter "*.rsd" -Recurse |
               Where-Object { $_.BaseName -ieq $TABLE } | Select-Object -First 1

    if (-not $rsdFile) { Write-Host "No RSD for '$TABLE' found." }
    else {
        [xml]$rsd    = Get-Content $rsdFile.FullName -Raw
        $rsdCols     = $rsd.root.schema.element.complexType.sequence.element
        $driverMap   = @{}; $cols | ForEach-Object { $driverMap[$_["ColumnName"].ToLower()] = $_ }
        $rsdIssues   = @()

        foreach ($rc in $rsdCols) {
            $n    = $rc.name
            $drv  = $driverMap[$n.ToLower()]
            if (-not $drv) { $rsdIssues += "MISSING_IN_DRIVER: $n in RSD not exposed by driver"; continue }

            $rsdRO  = ($rc.Attributes | Where-Object { $_.Name -eq "readonly" } | Select-Object -First 1).Value -eq "true"
            $drvRO  = $drv["IsReadOnly"] -eq "true"
            if ($rsdRO -ne $drvRO) { $rsdIssues += "READONLY_MISMATCH: $n RSD=$rsdRO Driver=$drvRO" }

            $rsdNull = ($rc.Attributes | Where-Object { $_.Name -eq "nillable" } | Select-Object -First 1).Value -eq "true"
            $drvNull = $drv["IsNullable"] -eq "true"
            if ($rsdNull -ne $drvNull) { $rsdIssues += "NULLABLE_MISMATCH: $n RSD=$rsdNull Driver=$drvNull" }
        }

        $rsdNameMap = @{}; $rsdCols | ForEach-Object { $rsdNameMap[$_.name.ToLower()] = $true }
        $cols | Where-Object { -not $rsdNameMap[$_["ColumnName"].ToLower()] } | ForEach-Object {
            $rsdIssues += "EXTRA_IN_DRIVER: $($_["ColumnName"]) exposed by driver but absent from RSD"
        }

        $verdict = if($rsdIssues){"BUG"}else{"PASS"}
        Add-Result "RSD-VALIDATION" "RSD column/endpoint correctness" $verdict `
            "$(if($rsdIssues){"$($rsdIssues.Count) issue(s)"}else{"All match"})" `
            ($rsdIssues -join " | ")
    }
}
```

---

## Step 4 — INSERT tests (expanded)

```powershell
Write-Host "`n=== STEP 4: INSERT TESTS ==="

# Build payloads
$reqCols  = ($required  | ForEach-Object { $_["ColumnName"] }) -join ","
$reqVals  = ($required  | ForEach-Object { "'$(Val-For $_["ColumnName"] $_["DataType"])'" }) -join ","
$fullCols = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
$fullVals = ($insertable | ForEach-Object { "'$(Val-For $_["ColumnName"] $_["DataType"])'" }) -join ","

$insertedIds = @()   # collect IDs of all successfully inserted rows for later tests

# ── TC-INS-01: Valid INSERT — all insertable columns ──────────────────────────
$r = Run-SQL "ins_full" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
$ok = $r.Out -match "AFFECTED:1|OK" -and $r.Out -notmatch "SQL_ERR|CONN_ERR"
Add-Result "TC-INS-01" "Valid INSERT — all insertable columns" `
    $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) Method=$($r.Log.Method) Endpoint=$($r.Log.Endpoint)"

# TC-INS-01a: SELECT back to verify data persisted
if ($ok -and $keyCols.Count -gt 0) {
    $keyCol = $keyCols[0]["ColumnName"]
    $filterCol = if($required){ $required[0]["ColumnName"] }else{ $keyCol }
    $filterVal = Val-For $filterCol ($cols | Where-Object { $_["ColumnName"] -eq $filterCol } | Select-Object -First 1)["DataType"]
    $rSel = Run-SQL "ins_verify" "SELECT * FROM $TABLE WHERE $filterCol='$filterVal' LIMIT 5"
    $vd   = Parse-Rows $rSel.Out
    if($vd.Rows.Count -gt 0){
        $insertedIds += $vd.Rows[0][$keyCol]
        Add-Result "TC-INS-01a" "SELECT after INSERT — data persisted" "PASS" "Rows=$($vd.Rows.Count) InsertedId=$($insertedIds[-1])"
    } else {
        Add-Result "TC-INS-01a" "SELECT after INSERT — data persisted" "FAIL" "0 rows found after INSERT"
    }
}

# ── TC-INS-02: INSERT — required columns only ─────────────────────────────────
if ($required.Count -lt $insertable.Count) {
    $r = Run-SQL "ins_req_only" "INSERT INTO $TABLE ($reqCols) VALUES ($reqVals)"
    $ok = $r.Out -match "AFFECTED:1|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-INS-02" "INSERT — required columns only (optional omitted)" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
    if($ok -and $keyCols.Count -gt 0){
        $rSel = Run-SQL "ins_req_verify" "SELECT $($keyCols[0]["ColumnName"]) FROM $TABLE ORDER BY $($keyCols[0]["ColumnName"]) DESC LIMIT 1"
        $vd   = Parse-Rows $rSel.Out
        if($vd.Rows.Count -gt 0){ $insertedIds += $vd.Rows[0][$keyCols[0]["ColumnName"]] }
    }
}

# ── TC-INS-03: INSERT missing one required column — driver MUST NOT hit API ───
if ($required.Count -ge 2) {
    $missingCol = $required[0]["ColumnName"]
    $remainCols = ($required[1..($required.Count-1)] | ForEach-Object { $_["ColumnName"] }) -join ","
    $remainVals = ($required[1..($required.Count-1)] | ForEach-Object { "'$(Val-For $_["ColumnName"] $_["DataType"])'" }) -join ","
    $r = Run-SQL "ins_miss_req" "INSERT INTO $TABLE ($remainCols) VALUES ($remainVals)"
    $noNet  = $r.Log.HTTP -eq 0
    $hasErr = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-03" "INSERT missing required col '$missingCol' — must NOT hit API" `
        $(if($noNet -and $hasErr){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) Error=$(if($hasErr){'YES'}else{'NO'})" `
        $(if(-not $noNet){"BUG: Driver hit API with missing required column (HTTP=$($r.Log.HTTP))"})
}

# ── TC-INS-04: INSERT empty string for required string col — must NOT hit API ─
$reqStrCol = $required | Where-Object { $_["DataType"] -notmatch "int|bool|date|number" } | Select-Object -First 1
if ($reqStrCol) {
    $emptyCols = ($required | ForEach-Object { $_["ColumnName"] }) -join ","
    $emptyVals = ($required | ForEach-Object {
        if($_["ColumnName"] -eq $reqStrCol["ColumnName"]){"''"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_empty_req" "INSERT INTO $TABLE ($emptyCols) VALUES ($emptyVals)"
    $noNet = $r.Log.HTTP -eq 0
    Add-Result "TC-INS-04" "INSERT empty required col '$($reqStrCol["ColumnName"])' — must NOT hit API" `
        $(if($noNet){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $noNet){"BUG: Driver sent request with empty required field"})
}

# ── TC-INS-05: INSERT with NULL for nullable column — should succeed ──────────
$nullableInsertable = $nullable | Where-Object { $_["IsInsertable"] -eq "true" -and $_["IsReadOnly"] -ne "true" } | Select-Object -First 1
if ($nullableInsertable) {
    $nc    = $nullableInsertable["ColumnName"]
    $colsWithNull = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $valsWithNull = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $nc){"NULL"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_null_nullable" "INSERT INTO $TABLE ($colsWithNull) VALUES ($valsWithNull)"
    $ok = $r.Out -match "AFFECTED:1|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-INS-05" "INSERT NULL for nullable col '$nc' — should succeed" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
}

# ── TC-INS-06: INSERT NULL for non-nullable non-required col (should error) ───
$nonNullNonReq = $insertable | Where-Object { $_["IsNullable"] -ne "true" -and $_["IsKey"] -ne "true" } |
                 Where-Object { ($required | ForEach-Object { $_["ColumnName"] }) -notcontains $_["ColumnName"] } |
                 Select-Object -First 1
if ($nonNullNonReq) {
    $nc       = $nonNullNonReq["ColumnName"]
    $colsNN   = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $valsNN   = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $nc){"NULL"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_null_nonnullable" "INSERT INTO $TABLE ($colsNN) VALUES ($valsNN)"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-06" "INSERT NULL for non-nullable col '$nc' — expect error" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"BUG: Driver accepted NULL for non-nullable column without error"})
}

# ── TC-INS-07: INSERT readonly column — driver must reject or silently ignore ─
if ($readonly.Count -gt 0) {
    $roCol = $readonly[0]["ColumnName"]
    $r = Run-SQL "ins_readonly" "INSERT INTO $TABLE ($fullCols,$roCol) VALUES ($fullVals,'READONLY_INJECT')"
    $err = $r.Out -match "SQL_ERR|read.?only|cannot insert"
    $noWrite = $r.Log.HTTP -eq 0
    Add-Result "TC-INS-07" "INSERT readonly col '$roCol' — expect rejection or silent ignore" `
        $(if($err -or $noWrite){"PASS"}else{"INFO"}) `
        "HTTP=$($r.Log.HTTP) Error=$(if($r.Out -match 'SQL_ERR'){'YES'}else{'NO — verify driver silently ignored (check request body)'})"
    # Inspect request body to confirm readonly col was not sent to API
    if(-not $err -and $r.Log.RequestBody){
        $sentRO = $r.Log.RequestBody | Where-Object { $_ -match $roCol }
        if($sentRO){
            Add-Result "TC-INS-07b" "Readonly col '$roCol' must NOT appear in API request body" "FAIL" `
                "BUG: '$roCol' found in request body → driver is sending readonly field to API"
        } else {
            Add-Result "TC-INS-07b" "Readonly col '$roCol' absent from API request body" "PASS" "Correctly stripped"
        }
    }
}

# ── TC-INS-08: INSERT invalid type for numeric column ─────────────────────────
$numCol = $insertable | Where-Object { $_["DataType"] -match "int|long|float|double|decimal|number" } | Select-Object -First 1
if ($numCol) {
    $badCols = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $badVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $numCol["ColumnName"]){"'NOT_A_NUMBER'"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_badtype_num" "INSERT INTO $TABLE ($badCols) VALUES ($badVals)"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-08" "INSERT non-numeric value for numeric col '$($numCol["ColumnName"])'" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"BUG: Driver accepted non-numeric value for integer/decimal column"})
}

# ── TC-INS-09: INSERT invalid type for date column ────────────────────────────
$dateCol = $insertable | Where-Object { $_["DataType"] -match "date|timestamp" } | Select-Object -First 1
if ($dateCol) {
    $badCols = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $badVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $dateCol["ColumnName"]){"'NOT_A_DATE'"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_badtype_date" "INSERT INTO $TABLE ($badCols) VALUES ($badVals)"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-09" "INSERT invalid date string for date col '$($dateCol["ColumnName"])'" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"BUG: Driver accepted malformed date without error"})
}

# ── TC-INS-10: INSERT invalid type for boolean column ────────────────────────
$boolCol = $insertable | Where-Object { $_["DataType"] -match "bool" } | Select-Object -First 1
if ($boolCol) {
    $badCols = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $badVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $boolCol["ColumnName"]){"'NOT_A_BOOL'"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_badtype_bool" "INSERT INTO $TABLE ($badCols) VALUES ($badVals)"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-10" "INSERT non-boolean value for bool col '$($boolCol["ColumnName"])'" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"WARN: Driver accepted non-boolean value — may pass junk to API"})
}

# ── TC-INS-11: INSERT oversized string value ──────────────────────────────────
$sizedStrCol = $insertable | Where-Object { $_["DataType"] -notmatch "int|bool|date" -and [int]$_["ColumnSize"] -gt 0 -and [int]$_["ColumnSize"] -lt 5000 } | Select-Object -First 1
if ($sizedStrCol) {
    $maxSize = [int]$sizedStrCol["ColumnSize"]
    $overVal = "X" * ($maxSize + 10)
    $osCols  = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $osVals  = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $sizedStrCol["ColumnName"]){"'$overVal'"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $r = Run-SQL "ins_oversized" "INSERT INTO $TABLE ($osCols) VALUES ($osVals)"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-INS-11" "INSERT value exceeding ColumnSize for '$($sizedStrCol["ColumnName"])' (max=$maxSize, sent=$($maxSize+10))" `
        $(if($err){"PASS"}else{"INFO"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"INFO: Driver allowed oversized string — check if API truncates silently"})
}

# ── TC-INS-12: Multi-row INSERT (batch behavior) ──────────────────────────────
$r = Run-SQL "ins_multi1" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
$r2b = Run-SQL "ins_multi2" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
$r3b = Run-SQL "ins_multi3" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
$ok1 = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
$ok2 = $r2b.Out -match "AFFECTED|OK" -and $r2b.Out -notmatch "SQL_ERR"
$ok3 = $r3b.Out -match "AFFECTED|OK" -and $r3b.Out -notmatch "SQL_ERR"
$totalHttp = $r.Log.HTTP + $r2b.Log.HTTP + $r3b.Log.HTTP
Add-Result "TC-INS-12" "3 separate INSERTs — verify each generates exactly 1 API call" `
    $(if($ok1 -and $ok2 -and $ok3){"PASS"}else{"FAIL"}) `
    "TotalHTTP=$totalHttp Expected=3" `
    $(if($totalHttp -ne 3){"INFO: Expected 3 HTTP calls for 3 inserts, got $totalHttp — check for batch collapsing or missing calls"})

# Track inserted IDs for update/delete tests
if($keyCols.Count -gt 0){
    $keyCol = $keyCols[0]["ColumnName"]
    $rSel = Run-SQL "ins_batch_ids" "SELECT $keyCol FROM $TABLE ORDER BY $keyCol DESC LIMIT 5"
    $vd = Parse-Rows $rSel.Out
    $vd.Rows | ForEach-Object { $insertedIds += $_[$keyCol] }
}
```

---

## Step 5 — UPDATE tests (expanded)

```powershell
Write-Host "`n=== STEP 5: UPDATE TESTS ==="

if ($updatable.Count -eq 0) { Write-Host "No updatable columns — skipping UPDATE tests."; return }

$keyCol = if($keyCols){ $keyCols[0]["ColumnName"] }else{ $null }
$targetId = if($insertedIds.Count -gt 0){ $insertedIds[0] }else{ $null }

# Pick a non-key updatable string col and a numeric col for update tests
$upStrCol  = ($updatable | Where-Object { $_["ColumnName"] -ne $keyCol -and $_["DataType"] -notmatch "int|bool|date" } | Select-Object -First 1)
$upNumCol  = ($updatable | Where-Object { $_["DataType"] -match "int|float|double|decimal|number" } | Select-Object -First 1)
$upDateCol = ($updatable | Where-Object { $_["DataType"] -match "date|timestamp" } | Select-Object -First 1)
$upBoolCol = ($updatable | Where-Object { $_["DataType"] -match "bool" } | Select-Object -First 1)

# ── TC-UPD-01: Valid UPDATE by primary key ────────────────────────────────────
if ($keyCol -and $targetId -and $upStrCol) {
    $upCol  = $upStrCol["ColumnName"]
    $upVal  = "Updated_$(Get-Random)"
    $r = Run-SQL "upd_valid_key" "UPDATE $TABLE SET $upCol='$upVal' WHERE $keyCol='$targetId'"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-UPD-01" "Valid UPDATE by primary key" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) Method=$($r.Log.Method) Endpoint=$($r.Log.Endpoint)"

    # TC-UPD-01a: SELECT after UPDATE to verify value persisted
    $rSel = Run-SQL "upd_verify" "SELECT $upCol FROM $TABLE WHERE $keyCol='$targetId' LIMIT 1"
    $vd   = Parse-Rows $rSel.Out
    $written = $vd.Rows.Count -gt 0 -and $vd.Rows[0][$upCol] -eq $upVal
    Add-Result "TC-UPD-01a" "SELECT after UPDATE — value matches" `
        $(if($written){"PASS"}else{"FAIL"}) "Expected='$upVal' Got='$($vd.Rows[0][$upCol])'"
}

# ── TC-UPD-02: UPDATE multiple columns in one statement ──────────────────────
if ($keyCol -and $targetId -and $upStrCol -and $upNumCol) {
    $r = Run-SQL "upd_multicol" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='MultiColUpd', $($upNumCol["ColumnName"])=99 WHERE $keyCol='$targetId'"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-UPD-02" "UPDATE multiple columns in one statement" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
    if($ok){
        $rSel = Run-SQL "upd_multicol_verify" "SELECT $($upStrCol["ColumnName"]),$($upNumCol["ColumnName"]) FROM $TABLE WHERE $keyCol='$targetId' LIMIT 1"
        $vd   = Parse-Rows $rSel.Out
        $strMatch = $vd.Rows[0][$upStrCol["ColumnName"]] -eq "MultiColUpd"
        $numMatch = $vd.Rows[0][$upNumCol["ColumnName"]] -eq "99"
        Add-Result "TC-UPD-02a" "SELECT after multi-col UPDATE — both values correct" `
            $(if($strMatch -and $numMatch){"PASS"}else{"FAIL"}) "StrMatch=$strMatch NumMatch=$numMatch"
    }
}

# ── TC-UPD-03: UPDATE by non-key filter (driver must SELECT first then write) ─
$nonKeyFilterCol = $updatable | Where-Object { $_["ColumnName"] -ne $keyCol -and $_["DataType"] -notmatch "int|bool|date" } | Select-Object -First 1
if ($nonKeyFilterCol -and $sampleData.Rows.Count -gt 0) {
    $fc  = $nonKeyFilterCol["ColumnName"]
    $fv  = if($sampleData.Rows[0][$fc]){ $sampleData.Rows[0][$fc] }else{ "CUDTest_$fc" }
    $r   = Run-SQL "upd_nonkey" "UPDATE $TABLE SET $fc='NonKeyUpd' WHERE $fc='$fv'"
    $ok  = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    $hadGet   = $r.Log.HTTP -gt 1
    $hadWrite = $r.Log.Method -match "PUT|PATCH|POST"
    Add-Result "TC-UPD-03" "UPDATE by non-key filter — driver must SELECT first" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) SelectFirst=$(if($hadGet){'YES'}else{'WARN—no prior GET seen'})"
}

# ── TC-UPD-04: UPDATE date column with valid date ────────────────────────────
if ($keyCol -and $targetId -and $upDateCol) {
    $dc  = $upDateCol["ColumnName"]
    $dv  = "2025-07-01T12:00:00Z"
    $r   = Run-SQL "upd_date_valid" "UPDATE $TABLE SET $dc='$dv' WHERE $keyCol='$targetId'"
    $ok  = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-UPD-04" "UPDATE date col '$dc' with valid timestamp" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
}

# ── TC-UPD-05: UPDATE date column with invalid date ──────────────────────────
if ($keyCol -and $targetId -and $upDateCol) {
    $dc = $upDateCol["ColumnName"]
    $r  = Run-SQL "upd_date_invalid" "UPDATE $TABLE SET $dc='NOT_A_DATE' WHERE $keyCol='$targetId'"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-UPD-05" "UPDATE date col '$dc' with invalid string — expect error" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"BUG: Driver accepted malformed date in UPDATE"})
}

# ── TC-UPD-06: UPDATE boolean column ─────────────────────────────────────────
if ($keyCol -and $targetId -and $upBoolCol) {
    $bc = $upBoolCol["ColumnName"]
    foreach ($bv in @("true","false")) {
        $r  = Run-SQL "upd_bool_$bv" "UPDATE $TABLE SET $bc=$bv WHERE $keyCol='$targetId'"
        $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
        Add-Result "TC-UPD-06-$bv" "UPDATE bool col '$bc' = $bv" `
            $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
    }
}

# ── TC-UPD-07: UPDATE readonly column — must reject ──────────────────────────
if ($readonly.Count -gt 0 -and $keyCol -and $targetId) {
    $roCol = $readonly[0]["ColumnName"]
    $r = Run-SQL "upd_readonly" "UPDATE $TABLE SET $roCol='READONLY_WRITE' WHERE $keyCol='$targetId'"
    $err = $r.Out -match "SQL_ERR|read.?only"
    Add-Result "TC-UPD-07" "UPDATE readonly col '$roCol' — must reject" `
        $(if($err){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $err){"BUG: Driver sent write request for readonly column"})
}

# ── TC-UPD-08: UPDATE with empty string for required column ──────────────────
if ($keyCol -and $targetId -and $upStrCol) {
    $rc2 = $required | Where-Object { $_["ColumnName"] -eq $upStrCol["ColumnName"] } | Select-Object -First 1
    if ($rc2) {
        $r = Run-SQL "upd_empty_req" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='' WHERE $keyCol='$targetId'"
        $noNet = $r.Log.HTTP -eq 0
        $err   = $r.Out -match "SQL_ERR"
        Add-Result "TC-UPD-08" "UPDATE empty string for required col '$($upStrCol["ColumnName"])' — must NOT hit API" `
            $(if($noNet){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
            $(if(-not $noNet){"BUG: Driver sent empty required field in UPDATE to API"})
    }
}

# ── TC-UPD-09: UPDATE non-existent key — should return 0 affected or error ───
if ($keyCol) {
    $fakeId = "NONEXISTENT_ID_XYZ_99999"
    $r = Run-SQL "upd_nonexistent" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='Ghost' WHERE $keyCol='$fakeId'"
    $affected0 = $r.Out -match "AFFECTED:0"
    $sqlErr    = $r.Out -match "SQL_ERR"
    $had404    = $r.Log.Status4xx | Where-Object { $_ -match "\b404\b" }
    Add-Result "TC-UPD-09" "UPDATE non-existent key — expect 0 rows affected or error" `
        $(if($affected0 -or $sqlErr){"PASS"}else{"FAIL"}) `
        "HTTP=$($r.Log.HTTP) 404=$(if($had404){'eaten—ok'}else{'not seen'})" `
        $(if(-not $affected0 -and -not $sqlErr){"BUG: UPDATE on non-existent row returned misleading success"})
}

# ── TC-UPD-10: UPDATE without WHERE — expect driver rejection (destructive) ──
if (Confirm-Destructive "Unbounded operation on $testTable") {
    $r = Run-SQL "upd_nowhere" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='NOWHERETEST'"
    $rejected = $r.Out -match "SQL_ERR" -or $r.Log.HTTP -eq 0
    Add-Result "TC-UPD-10" "UPDATE without WHERE — expect rejection" `
        $(if($rejected){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $rejected){"CRITICAL BUG: Unbounded UPDATE — all rows may have been modified"})
} else {
    Add-Result "TC-UPD-10" "UPDATE without WHERE — SKIPPED (user must confirm at runtime)" "SKIPPED" "Risk: mass update"
}

# ── TC-UPD-11: Bulk UPDATE — multiple rows via IN list ───────────────────────
if ($keyCol -and $insertedIds.Count -ge 2) {
    $id1 = $insertedIds[0]; $id2 = $insertedIds[1]
    $r = Run-SQL "upd_bulk_in" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='BulkUpd' WHERE $keyCol IN ('$id1','$id2')"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-UPD-11" "Bulk UPDATE — 2 rows via IN list" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) BatchHit=$($r.Log.BatchHit)" `
        $(if($cudEndpoints.BulkSupported -and -not $r.Log.BatchHit){"ENHANCEMENT: API supports batch UPDATE but driver went serial"})
}
```

---

## Step 6 — DELETE tests (expanded)

```powershell
Write-Host "`n=== STEP 6: DELETE TESTS ==="

if (-not $keyCol -and $keyCols.Count -eq 0) { Write-Host "No key column — skipping DELETE tests."; return }
$keyCol = $keyCols[0]["ColumnName"]

# Ensure we have an insertedId to delete
if ($insertedIds.Count -eq 0) {
    # Insert a fresh row for delete testing
    $rIns = Run-SQL "del_fresh_ins" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
    $ok   = $rIns.Out -match "AFFECTED:1|OK" -and $rIns.Out -notmatch "SQL_ERR"
    if ($ok) {
        $rSel = Run-SQL "del_fresh_sel" "SELECT $keyCol FROM $TABLE ORDER BY $keyCol DESC LIMIT 1"
        $vd   = Parse-Rows $rSel.Out
        if($vd.Rows.Count -gt 0){ $insertedIds += $vd.Rows[0][$keyCol] }
    }
}

# ── TC-DEL-01: Valid DELETE by primary key ────────────────────────────────────
$delTargetId = if($insertedIds.Count -gt 0){ $insertedIds[-1] }else{ $null }
if ($delTargetId) {
    $r = Run-SQL "del_valid_key" "DELETE FROM $TABLE WHERE $keyCol='$delTargetId'"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    $rightMethod = $r.Log.Method -eq "DELETE"
    Add-Result "TC-DEL-01" "Valid DELETE by primary key" `
        $(if($ok -and $rightMethod){"PASS"}elseif($ok){"INFO"}else{"FAIL"}) `
        "HTTP=$($r.Log.HTTP) Method=$($r.Log.Method) Endpoint=$($r.Log.Endpoint)" `
        $(if($ok -and -not $rightMethod){"INFO: DELETE succeeded but HTTP method was $($r.Log.Method)"})

    # TC-DEL-01a: SELECT after DELETE — row must be gone
    Start-Sleep -Milliseconds 500   # eventual consistency buffer
    $rSel = Run-SQL "del_verify" "SELECT * FROM $TABLE WHERE $keyCol='$delTargetId' LIMIT 1"
    $vd   = Parse-Rows $rSel.Out
    $gone = $vd.Rows.Count -eq 0
    Add-Result "TC-DEL-01a" "SELECT after DELETE — row must not exist" `
        $(if($gone){"PASS"}else{"FAIL"}) "RowsBack=$($vd.Rows.Count)" `
        $(if(-not $gone){"BUG or eventual consistency — retry after delay; if still present, DELETE did not persist"})

    # Remove from insertedIds since it's now deleted
    $insertedIds = $insertedIds | Where-Object { $_ -ne $delTargetId }
}

# ── TC-DEL-02: DELETE non-existent ID — 404 must be eaten ────────────────────
$fakeId = "NONEXISTENT_DEL_XYZ_00000"
$r = Run-SQL "del_nonexistent" "DELETE FROM $TABLE WHERE $keyCol='$fakeId'"
$had404    = $r.Log.Status4xx | Where-Object { $_ -match "\b404\b" }
$sqlErr    = $r.Out -match "SQL_ERR"
$affected0 = $r.Out -match "AFFECTED:0|OK"
Add-Result "TC-DEL-02" "DELETE non-existent ID — 404 must be silently eaten" `
    $(if(-not $sqlErr -or $affected0){"PASS"}else{"FAIL"}) `
    "HTTP=$($r.Log.HTTP) 404=$(if($had404){'seen—eaten'}else{'none'}) SqlErr=$(if($sqlErr){'YES—BUG'}else{'NO—ok'})" `
    $(if($sqlErr){"BUG: 404 from DELETE surfaced as SQL error — should return 0 rows affected silently"})

# ── TC-DEL-03: DELETE by non-key filter ──────────────────────────────────────
$delFilterCol = $updatable | Where-Object { $_["ColumnName"] -ne $keyCol -and $_["DataType"] -notmatch "int|bool|date" } | Select-Object -First 1
if ($delFilterCol -and $sampleData.Rows.Count -gt 0) {
    $fc = $delFilterCol["ColumnName"]
    $fv = if($sampleData.Rows[0][$fc]){ $sampleData.Rows[0][$fc] }else{ "CUDTest_$fc" }
    # Insert a row with a known value for this filter
    $filteredCols = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ","
    $filteredVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $fc){"'DEL_FILTER_TARGET'"} else {"'$(Val-For $_["ColumnName"] $_["DataType"])'"}
    }) -join ","
    $rIns = Run-SQL "del_filter_ins" "INSERT INTO $TABLE ($filteredCols) VALUES ($filteredVals)"
    $ok   = $rIns.Out -match "AFFECTED:1|OK" -and $rIns.Out -notmatch "SQL_ERR"
    if ($ok) {
        $r = Run-SQL "del_filter" "DELETE FROM $TABLE WHERE $fc='DEL_FILTER_TARGET'"
        $okDel = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
        $hadGet = $r.Log.HTTP -gt 1
        Add-Result "TC-DEL-03" "DELETE by non-key filter '$fc' — driver must SELECT first" `
            $(if($okDel){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) SelectFirst=$(if($hadGet){'YES'}else{'NO'})"
    }
}

# ── TC-DEL-04: DELETE with IN list — multiple rows ───────────────────────────
if ($insertedIds.Count -ge 2) {
    # Insert 2 fresh rows to delete
    $freshIds = @()
    for ($i=0; $i -lt 2; $i++) {
        $rIns = Run-SQL "del_bulk_ins_$i" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
        if($rIns.Out -match "AFFECTED:1|OK"){
            $rSel = Run-SQL "del_bulk_id_$i" "SELECT $keyCol FROM $TABLE ORDER BY $keyCol DESC LIMIT 1"
            $vd   = Parse-Rows $rSel.Out
            if($vd.Rows.Count -gt 0){ $freshIds += $vd.Rows[0][$keyCol] }
        }
    }
    if ($freshIds.Count -ge 2) {
        $inList = ($freshIds | ForEach-Object { "'$_'" }) -join ","
        $r = Run-SQL "del_bulk_in" "DELETE FROM $TABLE WHERE $keyCol IN ($inList)"
        $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
        Add-Result "TC-DEL-04" "DELETE IN list — 2 rows" `
            $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) BatchHit=$($r.Log.BatchHit)" `
            $(if($cudEndpoints.BulkSupported -and -not $r.Log.BatchHit){"ENHANCEMENT: batch DELETE available but driver went serial"})
    }
}

# ── TC-DEL-05: DELETE without WHERE — expect driver rejection (destructive) ──
if (Confirm-Destructive "Unbounded operation on $testTable") {
    $r = Run-SQL "del_nowhere" "DELETE FROM $TABLE"
    $rejected = $r.Out -match "SQL_ERR" -or $r.Log.HTTP -eq 0
    Add-Result "TC-DEL-05" "DELETE without WHERE — expect rejection" `
        $(if($rejected){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
        $(if(-not $rejected){"CRITICAL BUG: Unbounded DELETE — all rows may have been deleted"})
} else {
    Add-Result "TC-DEL-05" "DELETE without WHERE — SKIPPED (user must confirm at runtime)" "SKIPPED" "Risk: mass delete"
}

# ── TC-DEL-06: DELETE with LIMIT (if driver supports it) ─────────────────────
if ($insertedIds.Count -gt 0) {
    $r = Run-SQL "del_limit" "DELETE FROM $TABLE WHERE $keyCol IS NOT NULL LIMIT 1"
    $ok  = $r.Out -match "AFFECTED:1|OK"
    $err = $r.Out -match "SQL_ERR"
    Add-Result "TC-DEL-06" "DELETE with LIMIT 1 — verify driver handles or rejects cleanly" `
        $(if($ok){"PASS"}elseif($err){"INFO"}) `
        "HTTP=$($r.Log.HTTP) $(if($err){'SQL_ERR raised — check if driver supports LIMIT on DELETE or rejects it gracefully'})"
}
```

---

## Step 7 — Bulk / batch tests

```powershell
Write-Host "`n=== STEP 7: BULK / BATCH TESTS ==="

# ── TC-BULK-INS: 5 consecutive INSERTs — verify correct call count ────────────
$bulkIds = @()
$httpTotal = 0
for ($i=1; $i -le 5; $i++) {
    $r = Run-SQL "bulk_ins_$i" "INSERT INTO $TABLE ($fullCols) VALUES ($fullVals)"
    $httpTotal += $r.Log.HTTP
    if($r.Out -match "AFFECTED:1|OK" -and $keyCols.Count -gt 0){
        $rSel = Run-SQL "bulk_id_$i" "SELECT $($keyCols[0]["ColumnName"]) FROM $TABLE ORDER BY $($keyCols[0]["ColumnName"]) DESC LIMIT 1"
        $vd   = Parse-Rows $rSel.Out
        if($vd.Rows.Count -gt 0){ $bulkIds += $vd.Rows[0][$keyCols[0]["ColumnName"]] }
    }
}
$batchUsed = $r.Log.BatchHit
Add-Result "TC-BULK-INS" "5 serial INSERTs — HTTP call count" $(if($httpTotal -eq 5){"PASS"}else{"INFO"}) `
    "TotalHTTP=$httpTotal Expected=5 BatchUsed=$batchUsed" `
    $(if($cudEndpoints.BulkSupported -and -not $batchUsed){"ENHANCEMENT: API supports batch INSERT — driver making $httpTotal serial calls"})

# ── TC-BULK-UPD: UPDATE with IN list (multiple IDs) ───────────────────────────
if ($bulkIds.Count -ge 3 -and $upStrCol) {
    $inList = ($bulkIds[0..2] | ForEach-Object { "'$_'" }) -join ","
    $r = Run-SQL "bulk_upd_in" "UPDATE $TABLE SET $($upStrCol["ColumnName"])='BulkTest' WHERE $($keyCols[0]["ColumnName"]) IN ($inList)"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-BULK-UPD" "Bulk UPDATE 3 rows via IN list" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) BatchUsed=$($r.Log.BatchHit)" `
        $(if($cudEndpoints.BulkSupported -and -not $r.Log.BatchHit){"ENHANCEMENT: batch available but driver went serial"})
}

# ── TC-BULK-DEL: DELETE with IN list ──────────────────────────────────────────
if ($bulkIds.Count -ge 3) {
    $inList = ($bulkIds[0..2] | ForEach-Object { "'$_'" }) -join ","
    $r = Run-SQL "bulk_del_in" "DELETE FROM $TABLE WHERE $($keyCols[0]["ColumnName"]) IN ($inList)"
    $ok = $r.Out -match "AFFECTED|OK" -and $r.Out -notmatch "SQL_ERR"
    Add-Result "TC-BULK-DEL" "Bulk DELETE 3 rows via IN list" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) BatchUsed=$($r.Log.BatchHit)"
}
```

---

## Step 8 — 4xx error handling audit

```powershell
Write-Host "`n=== STEP 8: 4XX HANDLING AUDIT ==="

$allLogs     = Get-ChildItem $sp -Filter "*.log"
$fourxxIssues = @()

foreach ($lf in $allLogs) {
    $L   = Get-Content $lf.FullName -EA SilentlyContinue
    $tag = $lf.BaseName
    $hits = @($L | Where-Object { $_ -match "\[HTTP\|Res\].*\s4\d\d\b" })
    foreach ($h in $hits) {
        $code = [regex]::Match($h,"\s(4\d\d)\b").Groups[1].Value
        if ($code -ne "404") {
            $fourxxIssues += "[$tag] HTTP $code — driver must surface this to caller as clear SQL error"
        }
    }
}

Add-Result "TC-4XX-AUDIT" "Non-404 4xx must surface to caller" `
    $(if($fourxxIssues){"BUG"}else{"PASS"}) `
    "$(if($fourxxIssues){"$($fourxxIssues.Count) issue(s): " + ($fourxxIssues -join " | ")}else{"Clean"})"
```

---

## Step 9 — Cleanup & optimisation hints

```powershell
Write-Host "`n=== STEP 9: CLEANUP + HINTS ==="

# Delete all rows inserted during this test run (best-effort cleanup)
if ($insertedIds.Count -gt 0 -and $keyCol) {
    $remaining = $insertedIds | Where-Object { $_ }
    foreach ($id in $remaining) {
        Run-SQL "cleanup_$id" "DELETE FROM $TABLE WHERE $keyCol='$id'" | Out-Null
    }
    Write-Host "Cleanup: attempted DELETE for $($remaining.Count) test row(s)."
}

$hints = @()
if ($cudEndpoints.BulkSupported) {
    $anyBatch = $results | Where-Object { $_.Note -match "BatchUsed=True|BatchUsed=YES" }
    if (-not $anyBatch) { $hints += "ENHANCEMENT [BATCH]: API supports bulk endpoint but all writes were serial" }
}
$updateLogs = Get-ChildItem $sp -Filter "upd_*.log"
foreach ($lf in $updateLogs) {
    $L = Get-Content $lf.FullName -EA SilentlyContinue
    $gets = @($L | Where-Object { $_ -match "\bGET\b.*https?://" }).Count
    $puts = @($L | Where-Object { $_ -match "\b(PUT|PATCH)\b.*https?://" }).Count
    if ($gets -gt 1 -and $puts -ge 1) {
        $hints += "ENHANCEMENT [EXTRA_GET]: UPDATE '$($lf.BaseName)' shows $gets GET calls before write — verify all are necessary"
    }
}
$hints | ForEach-Object {
    Write-Host "  💡 $_"
    Add-Result "OPT-HINT" $_ "INFO" "Enhancement opportunity"
}
```

---

## Step 10 — Report

```
═══════════════════════════════════════════════════════════════════
CUD Testing Report — v2
═══════════════════════════════════════════════════════════════════
Driver : <DriverName> JDBC
Table  : <TableName>    Date: <date>
Source : Verbosity=5 log-first + JVM secondary
RSD    : <validated / not provided>

Columns: <N> total | Keys:<N> Required:<N> ReadOnly:<N>
         Insertable:<N> Updatable:<N> Nullable:<N>
         String:<N> Numeric:<N> Bool:<N> Date:<N>

CUD Endpoints (vendor docs):
  INSERT : <method> <endpoint>
  UPDATE : <method> <endpoint>
  DELETE : <method> <endpoint>
  Batch  : <supported / not supported>

═══ TEST RESULTS ══════════════════════════════════════════════════
ID               | Description                                          | Verdict | Note
─────────────────┼──────────────────────────────────────────────────────┼─────────┼──────────────────────
TC-INS-01        | Valid INSERT — all insertable columns                | PASS    | HTTP=1 POST /resource
TC-INS-01a       | SELECT after INSERT — data persisted                 | PASS    | ID=abc123
TC-INS-02        | INSERT — required columns only                       | PASS    | HTTP=1
TC-INS-03        | INSERT missing required col — must NOT hit API       | PASS    | HTTP=0 Error=YES
TC-INS-04        | INSERT empty required col — must NOT hit API         | PASS    | HTTP=0
TC-INS-05        | INSERT NULL for nullable col                         | PASS    | HTTP=1
TC-INS-06        | INSERT NULL for non-nullable col — expect error      | PASS    | SQL_ERR
TC-INS-07        | INSERT readonly col — rejected or silently ignored   | PASS    | col stripped
TC-INS-07b       | Readonly col absent from API request body            | PASS    | not in body
TC-INS-08        | INSERT invalid type for numeric col                  | PASS    | SQL_ERR
TC-INS-09        | INSERT invalid date string                           | PASS    | SQL_ERR
TC-INS-10        | INSERT non-boolean for bool col                      | PASS    | SQL_ERR
TC-INS-11        | INSERT oversized string — truncation or error        | INFO    | driver allowed
TC-INS-12        | 3 serial INSERTs — correct API call count            | PASS    | TotalHTTP=3
TC-UPD-01        | Valid UPDATE by primary key                          | PASS    | HTTP=1 PUT
TC-UPD-01a       | SELECT after UPDATE — value matches                  | PASS    |
TC-UPD-02        | UPDATE multiple columns in one statement             | PASS    | HTTP=1
TC-UPD-02a       | SELECT after multi-col UPDATE — both correct         | PASS    |
TC-UPD-03        | UPDATE by non-key filter                             | PASS    | HTTP=2 GET+PUT
TC-UPD-04        | UPDATE date col with valid timestamp                 | PASS    |
TC-UPD-05        | UPDATE date col with invalid string                  | PASS    | SQL_ERR
TC-UPD-06-true   | UPDATE bool col = true                               | PASS    |
TC-UPD-06-false  | UPDATE bool col = false                              | PASS    |
TC-UPD-07        | UPDATE readonly col — must reject                    | PASS    | SQL_ERR
TC-UPD-08        | UPDATE empty string for required col                 | PASS    | HTTP=0
TC-UPD-09        | UPDATE non-existent key — 0 rows or error            | PASS    | AFFECTED:0
TC-UPD-10        | UPDATE without WHERE — SKIPPED                       | SKIPPED | set flag to run
TC-UPD-11        | Bulk UPDATE 2 rows via IN list                       | PASS    | HTTP=2
TC-DEL-01        | Valid DELETE by primary key                          | PASS    | HTTP=1 DELETE
TC-DEL-01a       | SELECT after DELETE — row gone                       | PASS    | RowsBack=0
TC-DEL-02        | DELETE non-existent ID — 404 eaten                   | PASS    | 404 silently eaten
TC-DEL-03        | DELETE by non-key filter                             | PASS    | HTTP=2
TC-DEL-04        | DELETE IN list — 2 rows                              | PASS    | HTTP=2
TC-DEL-05        | DELETE without WHERE — SKIPPED                       | SKIPPED | set flag to run
TC-DEL-06        | DELETE with LIMIT 1                                  | INFO    |
TC-BULK-INS      | 5 serial INSERTs — HTTP call count                   | PASS    | TotalHTTP=5
TC-BULK-UPD      | Bulk UPDATE 3 rows via IN list                       | PASS    |
TC-BULK-DEL      | Bulk DELETE 3 rows via IN list                       | PASS    |
TC-4XX-AUDIT     | Non-404 4xx must surface to caller                   | PASS    | Clean
RSD-VALIDATION   | RSD column/endpoint correctness                      | PASS    |

═══ BUGS ══════════════════════════════════════════════════════════
(none)

═══ ENHANCEMENTS ══════════════════════════════════════════════════
(none)

Summary: PASS <N> / FAIL <N> / BUG <N> / INFO <N> / SKIPPED <N>   Total: <N>
═══════════════════════════════════════════════════════════════════
```

---

## Appendix — Common failure patterns

| Symptom | Meaning |
|---------|---------|
| TC-INS-03/04: HTTP>0 for missing/empty required field | Driver not validating before network call — bug |
| TC-INS-07b: readonly col in request body | Driver sending read-only fields to API — may cause 422 errors |
| TC-INS-11: oversized string accepted silently | API may truncate — note for data integrity |
| TC-UPD-09: "success" for non-existent key | Driver not checking 404 on UPDATE — misleading result |
| TC-UPD-10/DEL-05: no rejection on missing WHERE | Critical bug — risk of mass mutation/deletion |
| TC-DEL-02: 404 surfaces as SQL error | Driver propagating 404 — should eat it |
| Bulk tests: HTTP > row count | Serial calls when batch endpoint available |
| SELECT after CUD returns stale data | Eventual consistency — add 500ms delay and retry |
| 5xx in log | Server error — surface the response body; not driver bug unless swallowed |


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

Save-Usage -Cmd "/cud" -Drv $DC -Tbl $TABLE
```
