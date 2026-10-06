---
name: db-create-qpt
description: CREATE TABLE tests for CData DB driver — QueryPassThrough=True. Invoked by /qpttrue create.
---

# `/qpttrue create` — CREATE TABLE (QueryPassThrough=True)

**Mode: QPT=True** — the CREATE TABLE DDL is forwarded verbatim to the DB engine.

**QPT=True goal for CREATE:** Verify the driver passes DDL through unchanged and that the
schema reported back by `sys_tablecolumns` correctly reflects what the DB actually created.

```powershell
$script:currentQPT = "True"
Write-Host "=== CREATE TABLE — QueryPassThrough=True ==="

function Assert-QPTPassthrough([hashtable]$result, [string]$expectedSQLPrefix) {
    $sentLine = $result.Log.SentSQL | Select-Object -First 1
    if (-not $sentLine) {
        Add-Result "$($result.Tag)-QPT" "DDL forwarded verbatim to server" "INFO" "No SentSQL line in log"
        return
    }
    $prefix  = $expectedSQLPrefix.Trim().Substring(0, [Math]::Min(40, $expectedSQLPrefix.Trim().Length))
    $matches = $sentLine -match [regex]::Escape($prefix)
    Add-Result "$($result.Tag)-QPT" "DDL forwarded verbatim to server" `
        $(if($matches){"PASS"}else{"FAIL"}) "Expected prefix: '$prefix' | Log: $sentLine" `
        $(if(-not $matches){"BUG: Driver rewrote DDL before forwarding — QPT=True must never rewrite"})
}
```

---

## Phase C1 — Build DDL

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

## Phase C2 — Execute CREATE TABLE + QPT check

```powershell
$rCreate = Run-DB "create_table" $ddl "True"
$createOK = $rCreate.Out -match "\[OK\]|\[AFFECTED\]" -and $rCreate.Out -notmatch "\[SQL_ERR\]|\[CONN_ERR\]"
Add-Result "TC-CREATE-01" "CREATE TABLE with all data types" `
    $(if($createOK){"PASS"}else{"FAIL"}) "Table=$testTable Columns=$($colDefs.Count)"
Assert-QPTPassthrough $rCreate "CREATE TABLE $testTable"
```

---

## Phase C3 — Schema via sys_tablecolumns

```powershell
$rMeta      = Run-DB "create_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "True"
$schemaRows = @($rMeta.Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$schemaIssues = @()

foreach ($colDef in $colDefs) {
    $cn    = ($colDef -split "\s+")[0]
    $found = $schemaRows | Where-Object { $_["ColumnName"] -ieq $cn }
    if (-not $found) { $schemaIssues += "MISSING: '$cn' not in sys_tablecolumns" }
}
$pkRow = $schemaRows | Where-Object { $_["ColumnName"] -ieq "Id" }
if ($pkRow -and $pkRow["IsKey"] -ne "true") { $schemaIssues += "PK_MISMATCH: Id not IsKey=true" }

Add-Result "TC-CREATE-02" "Schema: sys_tablecolumns reflects all DDL columns" `
    $(if($schemaIssues){"FAIL"}else{"PASS"}) "Found=$($schemaRows.Count) Expected=$($colDefs.Count)" `
    ($schemaIssues -join " | ")
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
$rDesc    = Run-DB "create_describe" $descSQL "True"
$descRows = $rDesc.Parsed.Rows
Assert-QPTPassthrough $rDesc $descSQL

$nativeNames = $descRows | ForEach-Object {
    if($_["COLUMN_NAME"]){ $_["COLUMN_NAME"] }
    elseif($_["Field"]){ $_["Field"] }
    elseif($_["column_name"]){ $_["column_name"] }
    elseif($_["name"]){ $_["name"] }
}
$nativeMismatches = @()
$schemaRows | ForEach-Object {
    $cn = $_["ColumnName"]
    if($nativeNames -notcontains $cn){ $nativeMismatches += "NOT_IN_NATIVE: $cn" }
}
Add-Result "TC-CREATE-03" "Cross-check: sys_tablecolumns vs native DB DESCRIBE" `
    $(if($nativeMismatches){"FAIL"}else{"PASS"}) "Native=$($nativeNames.Count) CData=$($schemaRows.Count)" `
    ($nativeMismatches -join " | ")
```

---

## Phase C5 — Type mapping check

```powershell
$typeMismatches = @()
foreach ($sr in $schemaRows) {
    $cn  = $sr["ColumnName"]
    $cdt = $sr["DataType"]
    $nr  = $descRows | Where-Object { ($_.COLUMN_NAME -ieq $cn) -or ($_.Field -ieq $cn) -or ($_.column_name -ieq $cn) -or ($_.name -ieq $cn) } | Select-Object -First 1
    if (-not $nr) { continue }
    $nt = if($nr.DATA_TYPE){$nr.DATA_TYPE}elseif($nr.Type){$nr.Type}elseif($nr.data_type){$nr.data_type}else{"unknown"}
    Write-Host "  $cn : native=$nt → CData=$cdt"
    $wrong = ($nt -match "int" -and $cdt -match "varchar") -or
             ($nt -match "datetime|timestamp" -and $cdt -match "^varchar|^text") -or
             ($nt -match "bool|bit" -and $cdt -match "varchar")
    if ($wrong) { $typeMismatches += "TYPE_MISMATCH: $cn native=$nt CData=$cdt" }
}
Add-Result "TC-CREATE-04" "Type mapping: native types vs CData reported types" `
    $(if($typeMismatches){"FAIL"}else{"PASS"}) "$(if($typeMismatches){"$($typeMismatches.Count) mismatch(es)"}else{"All OK"})" `
    ($typeMismatches -join " | ")
```

---

## Phase C6 — Duplicate + invalid DDL errors

```powershell
$rDup = Run-DB "create_dup" "CREATE TABLE $testTable (Id INT PRIMARY KEY)" "True"
Add-Result "TC-CREATE-05" "Duplicate CREATE TABLE — expect SQL error" `
    $(if($rDup.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""

$rBad = Run-DB "create_bad" "CREATE TABLE BAD SYNTAX !@#$%" "True"
Add-Result "TC-CREATE-06" "Invalid DDL syntax — expect clear SQL error" `
    $(if($rBad.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
```


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qpttrue create" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
