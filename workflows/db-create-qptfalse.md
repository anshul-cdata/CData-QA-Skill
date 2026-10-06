---
name: db-create-qptfalse
description: CREATE TABLE tests for CData DB driver — QueryPassThrough=False. Invoked by /qptfalse create.
---

# `/qptfalse create` — CREATE TABLE (QueryPassThrough=False)

**Mode: QPT=False** — CData SQL engine intercepts the DDL before forwarding to the DB.

**QPT=False goal for CREATE:** Verify the CData SQL engine correctly handles DDL, that the
resulting schema matches what QPT=True creates (same columns, same types), and that the
rewrite log shows CData engaged.

```powershell
$script:currentQPT = "False"
Write-Host "=== CREATE TABLE — QueryPassThrough=False ==="

function Assert-RewriteLogged([hashtable]$result) {
    $rw = $result.Log.Rewritten | Select-Object -First 1
    Add-Result "$($result.Tag)-REWRITE" "CData rewrite visible in log" `
        "INFO" "$(if($rw){"Rewritten SQL: $rw"}else{"No rewrite line — may be passthrough internally for DDL"})"
}

function Compare-Schema([hashtable]$resultFalse, [hashtable]$resultTrue, [string]$desc) {
    $cntF = $resultFalse.Parsed.Count
    $cntT = $resultTrue.Parsed.Count
    Add-Result "CMP-$desc" "Schema consistent between QPT=False and QPT=True" `
        $(if($cntF -eq $cntT){"PASS"}else{"FAIL"}) "QPT=False=$cntF QPT=True=$cntT" `
        $(if($cntF -ne $cntT){"BUG: CData rewriter produced different schema than direct DDL"})
}
```

---

## Phase C1 — Build DDL (identical to QPT=True)

```powershell
Write-Host "`n=== CREATE TABLE: $testTable ==="

$idDef = switch($dbEngine) {
    "SQL Server" { "Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY" }
    "MySQL"      { "Id INT AUTO_INCREMENT NOT NULL PRIMARY KEY" }
    "PostgreSQL" { "Id SERIAL NOT NULL PRIMARY KEY" }
    "Oracle"     { "Id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY" }
    default      { "Id INTEGER PRIMARY KEY" }
}
$colDefs = @($idDef)

function Get-DefaultForType([string]$t) {
    switch -Regex ($t.ToLower()) {
        "int|long|small|tiny|number|bigint"     { return "0" }
        "decimal|numeric|float|double|real"      { return "0.0" }
        "bit|bool"                               { return "0" }
        "date(?!time)"                           { return switch($dbEngine){"SQL Server"{"'1900-01-01'"}default{"'1970-01-01'"}} }
        "datetime|timestamp"                     { return switch($dbEngine){"SQL Server"{"'1900-01-01 00:00:00'"}default{"'1970-01-01 00:00:00'"}} }
        "char|varchar|text|nchar|nvarchar|ntext" { return "''" }
        default                                  { return "NULL" }
    }
}

foreach ($typeName in $typeMap.Keys | Sort-Object) {
    $sqlType = $typeMap[$typeName]
    if ($typeName -eq "INT" -and $dbEngine -ne "MySQL") { continue }
    $nullable = if($typeName -match "TEXT|CLOB|BLOB|BINARY|VARBINARY|JSON|XML"){ "NULL" }
                else{ "NOT NULL DEFAULT $(Get-DefaultForType $typeName)" }
    $colDefs += "${typeName}Col $sqlType $nullable"
}
$colDefs += "NullableVarchar VARCHAR(255) NULL"
$colDefs += "RequiredVarchar VARCHAR(255) NOT NULL DEFAULT 'default_value'"

$ddl = "CREATE TABLE $testTable (`n  " + ($colDefs -join ",`n  ") + "`n)"
Write-Host "DDL:`n$ddl"
```

---

## Phase C2 — Execute CREATE TABLE in QPT=False + rewrite check

```powershell
$rCreateF = Run-DB "create_table_qptf" $ddl "False"
$createOK  = $rCreateF.Out -match "\[OK\]|\[AFFECTED\]" -and $rCreateF.Out -notmatch "\[SQL_ERR\]|\[CONN_ERR\]"
Add-Result "TC-CREATE-01" "CREATE TABLE (QPT=False)" `
    $(if($createOK){"PASS"}else{"FAIL"}) "Table=$testTable Columns=$($colDefs.Count)"
Assert-RewriteLogged $rCreateF
```

---

## Phase C3 — Schema via sys_tablecolumns + QPT=True cross-check

```powershell
$rMetaF = Run-DB "create_meta_qptf" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "False"
$rMetaT = Run-DB "create_meta_qptt" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "True"

$schemaRows   = @($rMetaF.Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$schemaRowsT  = @($rMetaT.Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$schemaIssues = @()
foreach ($colDef in $colDefs) {
    $cn    = ($colDef -split "\s+")[0]
    $found = $schemaRows | Where-Object { $_["ColumnName"] -ieq $cn }
    if (-not $found) { $schemaIssues += "MISSING: '$cn' not in sys_tablecolumns (QPT=False)" }
}
Add-Result "TC-CREATE-02" "Schema: sys_tablecolumns reflects all DDL columns (QPT=False)" `
    $(if($schemaIssues){"FAIL"}else{"PASS"}) "Found=$($schemaRows.Count) Expected=$($colDefs.Count)" `
    ($schemaIssues -join " | ")

# Cross-mode: column count must match
Compare-Schema $rMetaF $rMetaT "sys_tablecolumns_col_count"

# Type consistency between modes
$typeMapF = @{}; $schemaRows  | ForEach-Object { $typeMapF[$_["ColumnName"]] = $_["DataType"] }
$typeMapT = @{}; $schemaRowsT | ForEach-Object { $typeMapT[$_["ColumnName"]] = $_["DataType"] }
$typeDiffs = @()
foreach ($col in $typeMapF.Keys) {
    if ($typeMapT[$col] -and $typeMapT[$col] -ne $typeMapF[$col]) {
        $typeDiffs += "TYPE_DIFF: $col QPT=False=$($typeMapF[$col]) QPT=True=$($typeMapT[$col])"
    }
}
Add-Result "TC-CREATE-04" "Type consistency: QPT=False and QPT=True report same column types" `
    $(if($typeDiffs){"FAIL"}else{"PASS"}) "$(if($typeDiffs){"$($typeDiffs.Count) diff(s)"}else{"All match"})" `
    ($typeDiffs -join " | ")
```

---

## Phase C4 — Native DB schema cross-check

```powershell
$descSQL = switch($dbEngine) {
    "SQL Server" { "SELECT COLUMN_NAME,DATA_TYPE,IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='$testTable'" }
    "MySQL"      { "DESCRIBE $testTable" }
    "PostgreSQL" { "SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_name='$testTable'" }
    "Oracle"     { "SELECT COLUMN_NAME,DATA_TYPE,NULLABLE FROM USER_TAB_COLUMNS WHERE TABLE_NAME='$($testTable.ToUpper())'" }
    default      { "SELECT * FROM pragma_table_info('$testTable')" }
}
$rDescF   = Run-DB "create_describe_qptf" $descSQL "False"
$rDescT   = Run-DB "create_describe_qptt" $descSQL "True"
$descRows = $rDescF.Parsed.Rows

$nativeNames = $descRows | ForEach-Object {
    if($_["COLUMN_NAME"]){ $_["COLUMN_NAME"] }elseif($_["Field"]){ $_["Field"] }
    elseif($_["column_name"]){ $_["column_name"] }elseif($_["name"]){ $_["name"] }
}
$nativeMismatches = @()
$schemaRows | ForEach-Object {
    $cn = $_["ColumnName"]
    if($nativeNames -notcontains $cn){ $nativeMismatches += "NOT_IN_NATIVE: $cn" }
}
Add-Result "TC-CREATE-03" "Cross-check: sys_tablecolumns vs native DB DESCRIBE (QPT=False)" `
    $(if($nativeMismatches){"FAIL"}else{"PASS"}) "Native=$($nativeNames.Count) CData=$($schemaRows.Count)" `
    ($nativeMismatches -join " | ")

# Native describe: QPT=False vs QPT=True column count
Compare-Schema $rDescF $rDescT "native_describe_col_count"
```

---

## Phase C5 — Duplicate + invalid DDL errors

```powershell
$rDup = Run-DB "create_dup_qptf" "CREATE TABLE $testTable (Id INT PRIMARY KEY)" "False"
Add-Result "TC-CREATE-05" "Duplicate CREATE TABLE (QPT=False) — expect SQL error" `
    $(if($rDup.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""

$rBad = Run-DB "create_bad_qptf" "CREATE TABLE BAD SYNTAX !@#$%" "False"
Add-Result "TC-CREATE-06" "Invalid DDL syntax (QPT=False) — expect clear SQL error" `
    $(if($rBad.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
```


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qptfalse create" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
