---
name: db-insert-qptfalse
description: INSERT tests for CData DB driver — QueryPassThrough=False. Invoked by /qptfalse insert.
---

# `/qptfalse insert` — INSERT Tests (QueryPassThrough=False)

**Mode: QPT=False** — CData SQL engine processes INSERTs before forwarding.

**QPT=False goals for INSERT:** Every INSERT that succeeds in QPT=True must also succeed
in QPT=False and produce the same row. Every error case that fires in QPT=True must also
fire in QPT=False. Cross-mode row count after bulk insert must match.

```powershell
$script:currentQPT = "False"
Write-Host "=== INSERT — QueryPassThrough=False ==="

function Assert-RewriteLogged([hashtable]$result) {
    $rw = $result.Log.Rewritten | Select-Object -First 1
    Add-Result "$($result.Tag)-REWRITE" "CData rewrite visible in log" "INFO" `
        "$(if($rw){"Rewritten: $rw"}else{"No rewrite line — may be direct for DML"}) "
}

function Compare-InsertResult([string]$tag, [string]$sql, [string]$desc) {
    $rF = Run-DB "${tag}_qptf" $sql "False"
    $rT = Run-DB "${tag}_qptt" $sql "True"
    $okF = $rF.Out -match "\[OK\]|\[AFFECTED\]" -and $rF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rT.Out -match "\[OK\]|\[AFFECTED\]" -and $rT.Out -notmatch "\[SQL_ERR\]"
    $match = $okF -eq $okT
    Add-Result "TC-CMP-$tag" "$desc — outcome matches between QPT=False and QPT=True" `
        $(if($match){"PASS"}else{"FAIL"}) "QPT=False=$(if($okF){'OK'}else{'FAIL'}) QPT=True=$(if($okT){'OK'}else{'FAIL'})" `
        $(if(-not $match){"BUG: INSERT outcome differs between QPT modes"})
    return $rF
}
```

---

## Phase I0 — Discover insertable columns

```powershell
Write-Host "`n=== INSERT TESTS (QPT=False): $testTable ==="
$rMeta      = Run-DB "ins_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "False"
$schemaCols = @($rMeta.Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$insertable = $schemaCols | Where-Object { $_["IsKey"] -ne "true" -and $_["IsReadOnly"] -ne "true" }
$nullable   = $schemaCols | Where-Object { $_["IsNullable"] -eq "true" -and $_["IsKey"] -ne "true" }
$notNull    = $insertable | Where-Object { $_["IsNullable"] -ne "true" }
$keyCol     = ($schemaCols | Where-Object { $_["IsKey"] -eq "true" } | Select-Object -First 1)["ColumnName"]
$script:insertedIds = @()

function Get-InsertVal([string]$typeName, [string]$variant = "valid") {
    $valid = switch -Regex ($typeName.ToLower()) {
        "^int$|^bigint$|^smallint$|^tinyint$|^integer$|^serial$|^number$" { "42" }
        "decimal|numeric|float|double|real" { "3.14" }
        "bit|bool" { "1" }
        "char$" { "'CHAR______'" }
        "varchar|nvarchar|nchar" { "'Hello World'" }
        "text|ntext|clob|longtext|mediumtext" { "'Long text value for testing'" }
        "^date$" { "'2025-06-15'" }
        "^time$" { "'14:30:00'" }
        "datetime|smalldatetime" { "'2025-06-15 14:30:00'" }
        "datetime2|timestamp(?!tz)" { "'2025-06-15 14:30:00.000'" }
        "datetimeoffset|timestamptz" { "'2025-06-15 14:30:00+05:30'" }
        "uniqueidentifier|uuid" { "'550e8400-e29b-41d4-a716-446655440000'" }
        "json|jsonb" { "'{\"key\":\"value\"}'" }
        default { "'TestValue'" }
    }
    $invalid = switch -Regex ($typeName.ToLower()) {
        "int|bigint|decimal|float|double" { "'NOT_A_NUMBER'" }
        "bit|bool" { "'NOTBOOL'" }
        "date(?!time)" { "'NOT_A_DATE'" }
        "datetime|timestamp" { "'INVALID_DT'" }
        default { "'ValidFallback'" }
    }
    return if($variant -eq "invalid"){ $invalid }else{ $valid }
}

$colNames = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ", "
$colVals  = ($insertable | ForEach-Object { Get-InsertVal $_["DataType"] "valid" }) -join ", "
```

---

## Phase I1 — Core INSERT with QPT=False vs QPT=True comparison

```powershell
# TC-INS-01: Valid INSERT — compare both modes
$r = Compare-InsertResult "ins_valid" "INSERT INTO $testTable ($colNames) VALUES ($colVals)" "Valid INSERT — all columns"
Assert-RewriteLogged $r

$rId = Run-DB "ins_get_id" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" "False"
if($rId.Parsed.Rows.Count -gt 0){ $script:insertedIds += $rId.Parsed.Rows[0][$keyCol] }

# TC-INS-01a: SELECT back (QPT=False) — data correct
if($script:insertedIds.Count -gt 0){
    $rV = Run-DB "ins_verify_qptf" "SELECT * FROM $testTable WHERE $keyCol=$($script:insertedIds[-1])" "False"
    Add-Result "TC-INS-01a" "SELECT after INSERT (QPT=False) — row returned" `
        $(if($rV.Parsed.Count -gt 0){"PASS"}else{"FAIL"}) "Rows=$($rV.Parsed.Count)"
    # Verify SELECT itself matches QPT=True result
    $rVT = Run-DB "ins_verify_qptt" "SELECT * FROM $testTable WHERE $keyCol=$($script:insertedIds[-1])" "True"
    $cntMatch = $rV.Parsed.Count -eq $rVT.Parsed.Count
    Add-Result "TC-INS-01a-CMP" "SELECT after INSERT: row count QPT=False matches QPT=True" `
        $(if($cntMatch){"PASS"}else{"FAIL"}) "QPT=False=$($rV.Parsed.Count) QPT=True=$($rVT.Parsed.Count)"
}

# TC-INS-02: NOT NULL only
if($notNull.Count -gt 0){
    $nnNames = ($notNull | ForEach-Object { $_["ColumnName"] }) -join ", "
    $nnVals  = ($notNull | ForEach-Object { Get-InsertVal $_["DataType"] "valid" }) -join ", "
    Compare-InsertResult "ins_notnull" "INSERT INTO $testTable ($nnNames) VALUES ($nnVals)" "INSERT NOT NULL columns only"
}

# TC-INS-03: NULL for nullable column
$nullCol = $nullable | Select-Object -First 1
if($nullCol){
    $nc      = $nullCol["ColumnName"]
    $nullSQL = "INSERT INTO $testTable ($colNames) VALUES ($( ($insertable | ForEach-Object { if($_["ColumnName"] -eq $nc){"NULL"}else{Get-InsertVal $_["DataType"] "valid"} }) -join ", "))"
    Compare-InsertResult "ins_null_nullable" $nullSQL "INSERT NULL for nullable col '$nc'"
}

# TC-INS-04: NULL for NOT NULL col — both modes must error
$nnCol = $notNull | Select-Object -First 1
if($nnCol){
    $nc      = $nnCol["ColumnName"]
    $nullSQL = "INSERT INTO $testTable ($colNames) VALUES ($( ($insertable | ForEach-Object { if($_["ColumnName"] -eq $nc){"NULL"}else{Get-InsertVal $_["DataType"] "valid"} }) -join ", "))"
    Compare-InsertResult "ins_null_notnull" $nullSQL "INSERT NULL for NOT NULL col '$nc' — both modes error"
}

# TC-INS-05: Type validation — each non-string type
$typeCols = $insertable | Where-Object { $_["DataType"] -notmatch "varchar|text|char|clob" }
foreach ($tc in $typeCols) {
    $badVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $tc["ColumnName"]){ Get-InsertVal $tc["DataType"] "invalid" }
        else{ Get-InsertVal $_["DataType"] "valid" }
    }) -join ", "
    Compare-InsertResult "ins_badtype_$($tc["ColumnName"])" "INSERT INTO $testTable ($colNames) VALUES ($badVals)" "INSERT invalid $($tc["DataType"]) — both modes error"
}

# TC-INS-06: Duplicate PK
if($script:insertedIds.Count -gt 0){
    $dupSQL = "INSERT INTO $testTable (Id,$colNames) VALUES ($($script:insertedIds[0]),$colVals)"
    Compare-InsertResult "ins_dup_pk" $dupSQL "Duplicate PK INSERT — both modes error"
}

# TC-INS-07: Bulk — 5 rows, compare total row count between modes
$bulkCountF = 0; $bulkCountT = 0
for($i=1;$i -le 5;$i++){
    $rF = Run-DB "ins_bulk_f_$i" "INSERT INTO $testTable ($colNames) VALUES ($colVals)" "False"
    $rT = Run-DB "ins_bulk_t_$i" "INSERT INTO $testTable ($colNames) VALUES ($colVals)" "True"
    if($rF.Out -match "\[OK\]|\[AFFECTED\]" -and $rF.Out -notmatch "\[SQL_ERR\]"){ $bulkCountF++ }
    if($rT.Out -match "\[OK\]|\[AFFECTED\]" -and $rT.Out -notmatch "\[SQL_ERR\]"){ $bulkCountT++ }
}
Add-Result "TC-INS-08" "Bulk 5 INSERTs: QPT=False count matches QPT=True count" `
    $(if($bulkCountF -eq $bulkCountT -and $bulkCountF -eq 5){"PASS"}else{"FAIL"}) `
    "QPT=False=$bulkCountF QPT=True=$bulkCountT" `
    $(if($bulkCountF -ne $bulkCountT){"BUG: Different number of rows inserted between QPT modes"})

$rIds = Run-DB "ins_all_ids" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 20" "False"
$rIds.Parsed.Rows | ForEach-Object { $script:insertedIds += $_[$keyCol] }
$script:insertedIds = $script:insertedIds | Sort-Object -Unique


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qptfalse insert" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
