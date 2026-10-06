---
name: db-insert-qpt
description: INSERT tests for CData DB driver — QueryPassThrough=True. Invoked by /qpttrue insert.
---

# `/qpttrue insert` — INSERT Tests (QueryPassThrough=True)

**Mode: QPT=True** — INSERT statements forwarded verbatim to DB engine.

**QPT=True goals for INSERT:** Verify the driver forwards each INSERT unchanged, the DB
executes it, the driver correctly reports affected row count, and SELECT-back confirms
the data was written with no type conversion loss.

```powershell
$script:currentQPT = "True"
Write-Host "=== INSERT — QueryPassThrough=True ==="

function Assert-QPTPassthrough([hashtable]$result, [string]$expectedSQLPrefix) {
    $sentLine = $result.Log.SentSQL | Select-Object -First 1
    if (-not $sentLine) { Add-Result "$($result.Tag)-QPT" "INSERT forwarded verbatim" "INFO" "No SentSQL in log"; return }
    $prefix  = $expectedSQLPrefix.Trim().Substring(0, [Math]::Min(40, $expectedSQLPrefix.Trim().Length))
    $matches = $sentLine -match [regex]::Escape($prefix)
    Add-Result "$($result.Tag)-QPT" "INSERT forwarded verbatim to server" `
        $(if($matches){"PASS"}else{"FAIL"}) "Prefix: '$prefix' | Log: $sentLine" `
        $(if(-not $matches){"BUG: Driver rewrote INSERT statement — QPT=True must not rewrite"})
}
```

---

## Phase I0 — Discover insertable columns

```powershell
Write-Host "`n=== INSERT TESTS (QPT=True): $testTable ==="
$rMeta      = Run-DB "ins_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "True"
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
        "binary|varbinary|blob|bytea|mediumblob" { switch($dbEngine){ "PostgreSQL"{"E'\\\\x48656C6C6F'"} "MySQL"{"X'48656C6C6F'"} default{"0x48656C6C6F"} } }
        "^date$" { "'2025-06-15'" }
        "^time$" { "'14:30:00'" }
        "datetime|smalldatetime" { "'2025-06-15 14:30:00'" }
        "datetime2|timestamp(?!tz)" { "'2025-06-15 14:30:00.000'" }
        "datetimeoffset|timestamptz" { "'2025-06-15 14:30:00+05:30'" }
        "uniqueidentifier|uuid" { "'550e8400-e29b-41d4-a716-446655440000'" }
        "xml" { "'<root><item>test</item></root>'" }
        "json|jsonb" { "'{\"key\":\"value\"}'" }
        "enum" { "'A'" }
        default { "'TestValue'" }
    }
    $invalid = switch -Regex ($typeName.ToLower()) {
        "int|bigint|smallint|decimal|float|double|real|number" { "'NOT_A_NUMBER'" }
        "bit|bool" { "'NOTBOOL'" }
        "date(?!time)" { "'NOT_A_DATE'" }
        "datetime|timestamp" { "'INVALID_DT'" }
        "uuid|uniqueidentifier" { "'NOT_A_UUID'" }
        "json|jsonb" { "'{{{BAD_JSON'" }
        default { "'ValidFallback'" }
    }
    return if($variant -eq "invalid"){ $invalid }else{ $valid }
}

$colNames = ($insertable | ForEach-Object { $_["ColumnName"] }) -join ", "
$colVals  = ($insertable | ForEach-Object { Get-InsertVal $_["DataType"] "valid" }) -join ", "
```

---

## Phase I1 — Core INSERT tests

```powershell
# TC-INS-01: Valid INSERT — all columns
$sql = "INSERT INTO $testTable ($colNames) VALUES ($colVals)"
$r   = Run-DB "ins_valid_all" $sql "True"
$ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
Add-Result "TC-INS-01" "INSERT valid row — all columns with correct types" `
    $(if($ok){"PASS"}else{"FAIL"}) ""
Assert-QPTPassthrough $r "INSERT INTO $testTable"

# Capture ID
$rId = Run-DB "ins_get_id" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" "True"
if($rId.Parsed.Rows.Count -gt 0){ $script:insertedIds += $rId.Parsed.Rows[0][$keyCol] }

# TC-INS-01a: SELECT back — all values round-trip correctly
if($script:insertedIds.Count -gt 0){
    $insertedId = $script:insertedIds[-1]
    $rV = Run-DB "ins_verify_all" "SELECT * FROM $testTable WHERE $keyCol=$insertedId" "True"
    Add-Result "TC-INS-01a" "SELECT after INSERT — row returned" `
        $(if($rV.Parsed.Count -gt 0){"PASS"}else{"FAIL"}) "Rows=$($rV.Parsed.Count)"
    Assert-QPTPassthrough $rV "SELECT * FROM $testTable"
    # Per-column type round-trip check
    foreach ($col in $insertable) {
        $cn  = $col["ColumnName"]; $dt = $col["DataType"]
        $got = $rV.Parsed.Rows[0][$cn]
        $exp = (Get-InsertVal $dt "valid").Trim("'")
        $match = $got -ne $null -and ($got.Trim() -eq $exp -or $got -match [regex]::Escape($exp.Substring(0,[Math]::Min(8,$exp.Length))))
        if(-not $match){ Add-Result "TC-INS-01a-$cn" "Round-trip: $cn ($dt)" "FAIL" "Exp='$exp' Got='$got'" "TYPE_CONVERSION: value changed after INSERT+SELECT" }
    }
}

# TC-INS-02: INSERT — NOT NULL columns only
$nnNames = ($notNull | ForEach-Object { $_["ColumnName"] }) -join ", "
$nnVals  = ($notNull | ForEach-Object { Get-InsertVal $_["DataType"] "valid" }) -join ", "
if($notNull.Count -gt 0){
    $sql = "INSERT INTO $testTable ($nnNames) VALUES ($nnVals)"
    $r   = Run-DB "ins_notnull_only" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-INS-02" "INSERT — NOT NULL columns only (nullable omitted)" `
        $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r "INSERT INTO $testTable"
    if($ok){
        $rId = Run-DB "ins_notnull_id" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" "True"
        if($rId.Parsed.Rows.Count -gt 0){ $script:insertedIds += $rId.Parsed.Rows[0][$keyCol] }
    }
}

# TC-INS-03: INSERT explicit NULL for nullable col
$nullCol = $nullable | Select-Object -First 1
if($nullCol){
    $nc      = $nullCol["ColumnName"]
    $nullSQL = "INSERT INTO $testTable ($colNames) VALUES ($( ($insertable | ForEach-Object { if($_["ColumnName"] -eq $nc){"NULL"}else{Get-InsertVal $_["DataType"] "valid"} }) -join ", "))"
    $r = Run-DB "ins_null_nullable" $nullSQL "True"
    $ok = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-INS-03" "INSERT explicit NULL for nullable col '$nc'" `
        $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r "INSERT INTO $testTable"
    if($ok){
        $rId = Run-DB "ins_null_id" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" "True"
        if($rId.Parsed.Rows.Count -gt 0){
            $insertedId = $rId.Parsed.Rows[0][$keyCol]
            $script:insertedIds += $insertedId
            $rV  = Run-DB "ins_null_verify" "SELECT $nc FROM $testTable WHERE $keyCol=$insertedId" "True"
            $got = $rV.Parsed.Rows[0][$nc]
            Add-Result "TC-INS-03a" "SELECT after NULL INSERT — $nc is NULL" `
                $(if($got -eq $null){"PASS"}else{"FAIL"}) "Got='$got' Expected=NULL"
        }
    }
}

# TC-INS-04: INSERT NULL for NOT NULL col — expect error
$nnCol = $notNull | Select-Object -First 1
if($nnCol){
    $nc      = $nnCol["ColumnName"]
    $nullSQL = "INSERT INTO $testTable ($colNames) VALUES ($( ($insertable | ForEach-Object { if($_["ColumnName"] -eq $nc){"NULL"}else{Get-InsertVal $_["DataType"] "valid"} }) -join ", "))"
    $r = Run-DB "ins_null_notnull" $nullSQL "True"
    Add-Result "TC-INS-04" "INSERT NULL for NOT NULL col '$nc' — expect constraint error" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"BUG: Driver accepted NULL for NOT NULL column"})
}

# TC-INS-05: Type validation — one bad value per non-string type
$typeCols = $insertable | Where-Object { $_["DataType"] -notmatch "varchar|text|char|clob" }
foreach ($tc in $typeCols) {
    $badVals = ($insertable | ForEach-Object {
        if($_["ColumnName"] -eq $tc["ColumnName"]){ Get-InsertVal $tc["DataType"] "invalid" }
        else{ Get-InsertVal $_["DataType"] "valid" }
    }) -join ", "
    $r   = Run-DB "ins_badtype_$($tc["ColumnName"])" "INSERT INTO $testTable ($colNames) VALUES ($badVals)" "True"
    Add-Result "TC-INS-05-$($tc["ColumnName"])" "INSERT invalid value for $($tc["DataType"]) col '$($tc["ColumnName"])'" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"BUG: Driver accepted invalid $($tc["DataType"]) value"})
}

# TC-INS-06: Duplicate PK — expect constraint error
if($script:insertedIds.Count -gt 0){
    $dupId  = $script:insertedIds[0]
    $dupSQL = "INSERT INTO $testTable (Id,$colNames) VALUES ($dupId,$colVals)"
    $r = Run-DB "ins_dup_pk" $dupSQL "True"
    Add-Result "TC-INS-06" "INSERT duplicate primary key — expect constraint violation" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"BUG: Duplicate PK insert succeeded"})
}

# TC-INS-07: Special characters in string column
$strCol = $insertable | Where-Object { $_["DataType"] -match "varchar|nvarchar" } | Select-Object -First 1
if($strCol){
    $specialCases = @(
        @{ name="single_quote"; val="It''s a test" }
        @{ name="unicode";      val="héllo wörld" }
        @{ name="html";         val="<script>alert(1)</script>" }
        @{ name="semicolon";    val="select; drop table" }
    )
    foreach ($sc in $specialCases) {
        $scVals = ($insertable | ForEach-Object {
            if($_["ColumnName"] -eq $strCol["ColumnName"]){"'$($sc.val)'"}
            else{ Get-InsertVal $_["DataType"] "valid" }
        }) -join ", "
        $sql = "INSERT INTO $testTable ($colNames) VALUES ($scVals)"
        $r   = Run-DB "ins_special_$($sc.name)" $sql "True"
        $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
        Add-Result "TC-INS-07-$($sc.name)" "INSERT special char: $($sc.name)" `
            $(if($ok){"PASS"}else{"FAIL"}) ""
        Assert-QPTPassthrough $r "INSERT INTO $testTable"
    }
}

# TC-INS-08: Bulk — 5 rows, verify call count
$bulkCount = 0
for($i=1;$i -le 5;$i++){
    $r = Run-DB "ins_bulk_$i" "INSERT INTO $testTable ($colNames) VALUES ($colVals)" "True"
    $ok = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    if($ok){ $bulkCount++ }
}
$rCnt = Run-DB "ins_bulk_count" "SELECT COUNT(*) AS Cnt FROM $testTable" "True"
Add-Result "TC-INS-08" "Bulk: 5 consecutive INSERTs" `
    $(if($bulkCount -eq 5){"PASS"}else{"FAIL"}) "Succeeded=$bulkCount/5 TotalRows=$($rCnt.Parsed.Rows[0]["Cnt"])"

# Collect all IDs
$rIds = Run-DB "ins_all_ids" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 20" "True"
$rIds.Parsed.Rows | ForEach-Object { $script:insertedIds += $_[$keyCol] }
$script:insertedIds = $script:insertedIds | Sort-Object -Unique


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qpttrue insert" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
