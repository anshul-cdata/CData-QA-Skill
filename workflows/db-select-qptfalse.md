---
name: db-select-qptfalse
description: SELECT tests for CData DB driver with QueryPassThrough=False. Invoked by /qptfalse select or as part of /qptfalse full suite.
---

# `/qptfalse select` — SELECT Tests (QueryPassThrough=False)

**Mode: QPT=False** — queries are parsed and rewritten by the CData SQL engine before being
forwarded to the database.

Goals for this mode:
1. Rewritten SQL is **semantically equivalent** — same rows as QPT=True.
2. All SQL constructs survive the rewrite (aliases, expressions, JOINs, GROUP BY, HAVING, subqueries).
3. CData's parser handles edge cases cleanly (reserved words, NULLs, special operators).
4. Row counts and values match QPT=True runs of the same query.
5. Rewrite-specific SQL features work correctly (CASE, string functions, date functions, COALESCE).

---

## Setup

```powershell
$script:currentQPT = "False"
Write-Host "=== SELECT — QueryPassThrough=False ==="
```

**Comparison helper** — run the same query in both modes and compare row counts:

```powershell
function Compare-Modes([string]$tag, [string]$sql, [string]$desc) {
    $rTrue  = Run-DB "${tag}_qptt" $sql "True"
    $rFalse = Run-DB "${tag}_qptf" $sql "False"
    $match  = $rTrue.Parsed.Count -eq $rFalse.Parsed.Count
    Add-Result "TC-CMP-$tag" "$desc — QPT=True vs QPT=False row count matches" `
        $(if($match){"PASS"}else{"FAIL"}) `
        "QPT=True=$($rTrue.Parsed.Count) QPT=False=$($rFalse.Parsed.Count)" `
        $(if(-not $match){"BUG: CData rewriter changed query semantics — row count differs between modes"})
    return $rFalse
}
```

---

## Phase S0 — Baseline + cross-mode comparison

```powershell
Write-Host "`n=== SELECT TESTS (QPT=False): $testTable ==="

$rBase      = Run-DB "sel_baseline" "SELECT * FROM $testTable" "False"
$baseRows   = $rBase.Parsed.Rows
$baseCnt    = $rBase.Parsed.Count
$schemaCols = $rBase.Parsed.Cols
$schemaTypes = $rBase.Parsed.Types

Add-Result "TC-SEL-00" "Baseline SELECT * (QPT=False)" `
    $(if($baseCnt -gt 0){"PASS"}else{"FAIL"}) "Rows=$baseCnt Cols=$($schemaCols.Count)"

# Cross-mode: baseline row count must match QPT=True
$rBaseTrue = Run-DB "sel_baseline_true" "SELECT * FROM $testTable" "True"
$baseMatch = $baseCnt -eq $rBaseTrue.Parsed.Count
Add-Result "TC-SEL-00-CMP" "Baseline row count: QPT=False matches QPT=True" `
    $(if($baseMatch){"PASS"}else{"FAIL"}) `
    "QPT=False=$baseCnt QPT=True=$($rBaseTrue.Parsed.Count)"

function First-Col([string[]]$patterns){
    foreach($p in $patterns){
        $m = $schemaCols | Where-Object { $_ -match $p -and $baseRows[0][$_] } | Select-Object -First 1
        if($m){ return $m }
    }
    return $null
}
$strCol  = First-Col @("varchar","nvarchar","text","char")
$numCol  = First-Col @("int","bigint","smallint","decimal","numeric","float","double","real","number")
$dateCol = First-Col @("datetime","timestamp","date")
$boolCol = First-Col @("bit","bool")
$keyCol  = ($schemaCols | Where-Object { $_ -ieq "Id" } | Select-Object -First 1)
if(-not $keyCol){ $keyCol = $schemaCols[0] }

$strVal  = if($strCol  -and $baseRows[0][$strCol]) { $baseRows[0][$strCol] }  else { "Hello World" }
$numVal  = if($numCol  -and $baseRows[0][$numCol]) { $baseRows[0][$numCol] }  else { "42" }
$dateVal = if($dateCol -and $baseRows[0][$dateCol]){ $baseRows[0][$dateCol].Substring(0,10) } else { "2025-06-15" }
$keyVal  = if($baseRows[0][$keyCol]){ $baseRows[0][$keyCol] }else{ "1" }
```

---

## Phase S1 — Projection (with cross-mode comparison)

```powershell
# TC-SEL-01: SELECT two columns
Compare-Modes "proj_2col" "SELECT $keyCol, $strCol FROM $testTable" "Projection: SELECT two columns"

# TC-SEL-02: Column alias — verify CData passes alias through
$r = Run-DB "sel_alias" "SELECT $keyCol AS PrimaryKey, $strCol AS Label FROM $testTable LIMIT 3" "False"
$aliasOK = $r.Parsed.Cols -contains "PrimaryKey" -or $r.Parsed.Cols -contains "primarykey"
Add-Result "TC-SEL-02" "Projection: column alias (AS) — alias preserved after rewrite" `
    $(if($aliasOK){"PASS"}else{"FAIL"}) "Cols=$($r.Parsed.Cols -join ',')" `
    $(if(-not $aliasOK){"BUG: CData SQL engine dropped or renamed the alias"})

# TC-SEL-03: Arithmetic expression
if($numCol){
    $r = Compare-Modes "expr_arith" "SELECT $numCol, $numCol * 2 AS Doubled FROM $testTable LIMIT 5" "Arithmetic expression (* 2)"
    $hasDoubled = $r.Parsed.Cols -contains "Doubled" -or $r.Parsed.Cols.Count -ge 2
    Add-Result "TC-SEL-03b" "Expression: Doubled alias present after rewrite" `
        $(if($hasDoubled){"PASS"}else{"INFO"}) "Cols=$($r.Parsed.Cols -join ',')"
}

# TC-SEL-04: COUNT(*)
Compare-Modes "count_star" "SELECT COUNT(*) AS Total FROM $testTable" "COUNT(*)"

# TC-SEL-05: DISTINCT
if($strCol){
    Compare-Modes "distinct" "SELECT DISTINCT $strCol FROM $testTable" "DISTINCT"
}
```

---

## Phase S2 — WHERE operators (with cross-mode comparison)

```powershell
if($strCol){
    $strCases = @(
        @{ tag="eq_str";    sql="SELECT * FROM $testTable WHERE $strCol = '$strVal'";                              desc="WHERE string =" }
        @{ tag="neq_str";   sql="SELECT * FROM $testTable WHERE $strCol != '$strVal'";                             desc="WHERE string !=" }
        @{ tag="in_str";    sql="SELECT * FROM $testTable WHERE $strCol IN ('$strVal','NONEXISTENT_XYZ')";         desc="WHERE string IN" }
        @{ tag="notin_str"; sql="SELECT * FROM $testTable WHERE $strCol NOT IN ('NONEXISTENT_XYZ')";               desc="WHERE string NOT IN" }
        @{ tag="like_pre";  sql="SELECT * FROM $testTable WHERE $strCol LIKE '$($strVal.Substring(0,[Math]::Min(3,$strVal.Length)))%'"; desc="WHERE LIKE prefix%" }
        @{ tag="like_con";  sql="SELECT * FROM $testTable WHERE $strCol LIKE '%$($strVal.Substring(0,[Math]::Min(3,$strVal.Length)))%'"; desc="WHERE LIKE %contains%" }
        @{ tag="isnull";    sql="SELECT * FROM $testTable WHERE NullableVarchar IS NULL";                          desc="WHERE IS NULL" }
        @{ tag="isnotnull"; sql="SELECT * FROM $testTable WHERE NullableVarchar IS NOT NULL";                      desc="WHERE IS NOT NULL" }
    )
    foreach($c in $strCases){ Compare-Modes $c.tag $c.sql $c.desc }
}

if($numCol){
    $numCases = @(
        @{ tag="num_eq";   sql="SELECT * FROM $testTable WHERE $numCol = $numVal";             desc="WHERE numeric =" }
        @{ tag="num_neq";  sql="SELECT * FROM $testTable WHERE $numCol != $numVal";            desc="WHERE numeric !=" }
        @{ tag="num_gt";   sql="SELECT * FROM $testTable WHERE $numCol > 0";                   desc="WHERE numeric >" }
        @{ tag="num_gte";  sql="SELECT * FROM $testTable WHERE $numCol >= 0";                  desc="WHERE numeric >=" }
        @{ tag="num_lt";   sql="SELECT * FROM $testTable WHERE $numCol < 999999";              desc="WHERE numeric <" }
        @{ tag="num_lte";  sql="SELECT * FROM $testTable WHERE $numCol <= 999999";             desc="WHERE numeric <=" }
        @{ tag="num_btwn"; sql="SELECT * FROM $testTable WHERE $numCol BETWEEN 0 AND 999999";  desc="WHERE numeric BETWEEN" }
        @{ tag="num_in";   sql="SELECT * FROM $testTable WHERE $numCol IN (0,$numVal,999999)"; desc="WHERE numeric IN" }
    )
    foreach($c in $numCases){ Compare-Modes $c.tag $c.sql $c.desc }
}

if($dateCol){
    $d1 = "2020-01-01"; $d2 = "2030-12-31"
    $dateCases = @(
        @{ tag="date_eq";   sql="SELECT * FROM $testTable WHERE $dateCol = '$dateVal'";             desc="WHERE date =" }
        @{ tag="date_gt";   sql="SELECT * FROM $testTable WHERE $dateCol > '$d1'";                 desc="WHERE date >" }
        @{ tag="date_gte";  sql="SELECT * FROM $testTable WHERE $dateCol >= '$d1'";                desc="WHERE date >=" }
        @{ tag="date_lt";   sql="SELECT * FROM $testTable WHERE $dateCol < '$d2'";                 desc="WHERE date <" }
        @{ tag="date_lte";  sql="SELECT * FROM $testTable WHERE $dateCol <= '$d2'";                desc="WHERE date <=" }
        @{ tag="date_btwn"; sql="SELECT * FROM $testTable WHERE $dateCol BETWEEN '$d1' AND '$d2'"; desc="WHERE date BETWEEN" }
    )
    foreach($c in $dateCases){ Compare-Modes $c.tag $c.sql $c.desc }
}

if($boolCol){
    foreach($bv in @("1","0")){
        Compare-Modes "bool_$bv" "SELECT * FROM $testTable WHERE $boolCol = $bv" "WHERE bool = $bv"
    }
}

if($strCol -and $numCol){
    $compCases = @(
        @{ tag="and";    sql="SELECT * FROM $testTable WHERE $strCol IS NOT NULL AND $numCol >= 0";               desc="WHERE AND" }
        @{ tag="or";     sql="SELECT * FROM $testTable WHERE $strCol = '$strVal' OR $numCol > 999999";            desc="WHERE OR" }
        @{ tag="and_or"; sql="SELECT * FROM $testTable WHERE ($strCol IS NOT NULL) AND ($numCol > 0 OR $numCol = 0)"; desc="WHERE (AND) with (OR)" }
        @{ tag="not";    sql="SELECT * FROM $testTable WHERE NOT ($strCol = 'NONEXISTENT_XYZ')";                  desc="WHERE NOT" }
    )
    foreach($c in $compCases){ Compare-Modes $c.tag $c.sql $c.desc }
}
```

---

## Phase S3 — ORDER BY (with cross-mode comparison)

```powershell
if($numCol){
    Compare-Modes "order_asc"   "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol ASC"          "ORDER BY numeric ASC"
    Compare-Modes "order_desc"  "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol DESC"         "ORDER BY numeric DESC"
    Compare-Modes "order_limit" "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol ASC LIMIT 3"  "ORDER BY + LIMIT 3"
}
if($strCol){
    Compare-Modes "order_str" "SELECT $keyCol, $strCol FROM $testTable ORDER BY $strCol ASC" "ORDER BY string"
}
if($strCol -and $numCol){
    Compare-Modes "order_multi" "SELECT $keyCol, $strCol, $numCol FROM $testTable ORDER BY $strCol ASC, $numCol DESC" "ORDER BY two columns"
}
```

---

## Phase S4 — LIMIT and OFFSET (with cross-mode comparison)

```powershell
foreach($lim in @(1,3,5)){
    $r = Run-DB "limit_$lim" "SELECT * FROM $testTable LIMIT $lim" "False"
    Add-Result "TC-SEL-7$lim" "LIMIT $lim (QPT=False)" `
        $(if($r.Parsed.Count -le $lim){"PASS"}else{"FAIL"}) "Returned=$($r.Parsed.Count) Max=$lim" `
        $(if($r.Parsed.Count -gt $lim){"BUG: LIMIT $lim not honoured by CData SQL engine"})
}

if($baseCnt -gt 2){
    Compare-Modes "offset_p1" "SELECT $keyCol FROM $testTable ORDER BY $keyCol LIMIT 2 OFFSET 0" "Pagination page 1"
    Compare-Modes "offset_p2" "SELECT $keyCol FROM $testTable ORDER BY $keyCol LIMIT 2 OFFSET 2" "Pagination page 2"
}

$r = Run-DB "limit_0" "SELECT * FROM $testTable LIMIT 0" "False"
Add-Result "TC-SEL-74" "LIMIT 0 — returns 0 rows (QPT=False)" `
    $(if($r.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) "Returned=$($r.Parsed.Count)"
```

---

## Phase S5 — Aggregates and GROUP BY (with cross-mode comparison)

```powershell
if($numCol){
    foreach($a in @("COUNT(*)","SUM($numCol)","AVG($numCol)","MIN($numCol)","MAX($numCol)")){
        $alias = ($a -split "\(")[0]
        Compare-Modes "agg_$alias" "SELECT $a AS $alias FROM $testTable" "Aggregate: $a"
    }
}
if($strCol -and $numCol){
    Compare-Modes "groupby"  "SELECT $strCol, COUNT(*) AS Cnt, SUM($numCol) AS Total FROM $testTable GROUP BY $strCol" "GROUP BY + COUNT + SUM"
    Compare-Modes "having"   "SELECT $strCol, COUNT(*) AS Cnt FROM $testTable GROUP BY $strCol HAVING COUNT(*) >= 1"   "GROUP BY + HAVING"
}
```

---

## Phase S6 — Subqueries (with cross-mode comparison)

```powershell
if($numCol){
    Compare-Modes "subq_where" "SELECT * FROM $testTable WHERE $numCol = (SELECT MAX($numCol) FROM $testTable)"                                       "Subquery in WHERE"
    Compare-Modes "subq_from"  "SELECT sub.$keyCol, sub.$numCol FROM (SELECT $keyCol, $numCol FROM $testTable WHERE $numCol >= 0) AS sub"             "Derived table in FROM"
    Compare-Modes "subq_exists" "SELECT * FROM $testTable t WHERE EXISTS (SELECT 1 FROM $testTable t2 WHERE t2.$keyCol = t.$keyCol)"                 "EXISTS subquery"
}
```

---

## Phase S7 — QPT=False specific: rewrite-only SQL features

These tests exercise SQL that the CData rewriter must transform — they have no QPT=True
equivalent. Each is run in QPT=False only and verified on its own result.

```powershell
Write-Host "`n--- Rewrite-specific tests (QPT=False only) ---"

# RW-01: Reserved word as quoted alias
$r = Run-DB "rw_reserved" "SELECT $keyCol AS [order], $strCol AS [select] FROM $testTable LIMIT 3" "False"
Add-Result "TC-RW-01" "Rewrite: reserved word as quoted column alias ([order],[select])" `
    $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "" `
    $(if($r.Out -match "\[SQL_ERR\]"){"BUG: CData SQL engine failed to handle quoted reserved word alias"})

# RW-02: CASE expression
if($numCol){
    $sql = "SELECT $keyCol, CASE WHEN $numCol > 50 THEN 'High' WHEN $numCol > 10 THEN 'Mid' ELSE 'Low' END AS Band FROM $testTable"
    $r   = Run-DB "rw_case" $sql "False"
    $hasBand = $r.Parsed.Cols -contains "Band" -or $r.Parsed.Cols -contains "band"
    Add-Result "TC-RW-02" "Rewrite: CASE expression with alias 'Band'" `
        $(if($hasBand -and -not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) `
        "Cols=$($r.Parsed.Cols -join ',') Rows=$($r.Parsed.Count)"
}

# RW-03: String functions (DB-engine aware)
if($strCol){
    $upperFn   = if($dbEngine -eq "SQL Server"){"LEN"}else{"LENGTH"}
    $sql = "SELECT $strCol, UPPER($strCol) AS UpperVal, $upperFn($strCol) AS Len FROM $testTable LIMIT 5"
    $r   = Run-DB "rw_str_fn" $sql "False"
    Add-Result "TC-RW-03" "Rewrite: string functions UPPER + $upperFn" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
}

# RW-04: Date extraction functions
if($dateCol){
    $yearFn  = if($dbEngine -eq "Oracle"){"EXTRACT(YEAR FROM $dateCol)"}else{"YEAR($dateCol)"}
    $monthFn = if($dbEngine -eq "Oracle"){"EXTRACT(MONTH FROM $dateCol)"}else{"MONTH($dateCol)"}
    $sql = "SELECT $keyCol, $dateCol, $yearFn AS Yr, $monthFn AS Mo FROM $testTable LIMIT 5"
    $r   = Run-DB "rw_date_fn" $sql "False"
    Add-Result "TC-RW-04" "Rewrite: date extraction ($yearFn, $monthFn)" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
}

# RW-05: COALESCE / ISNULL for NULL substitution
$nullFn  = if($dbEngine -eq "SQL Server"){"ISNULL(NullableVarchar,'DEFAULT')"}else{"COALESCE(NullableVarchar,'DEFAULT')"}
$sql = "SELECT $keyCol, $nullFn AS WithDefault FROM $testTable LIMIT 5"
$r   = Run-DB "rw_coalesce" $sql "False"
$hasNullsLeft = $r.Parsed.Rows | Where-Object { $_["WithDefault"] -eq $null }
Add-Result "TC-RW-05" "Rewrite: $nullFn replaces NULLs with DEFAULT" `
    $(if(-not $hasNullsLeft -and -not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) `
    "NullsRemaining=$($hasNullsLeft.Count)"

# RW-06: CONCAT / string concatenation
if($strCol){
    $concatSQL = if($dbEngine -eq "SQL Server"){
        "SELECT $keyCol, $strCol + '_suffix' AS Concat FROM $testTable LIMIT 5"
    } else {
        "SELECT $keyCol, CONCAT($strCol, '_suffix') AS Concat FROM $testTable LIMIT 5"
    }
    $r = Run-DB "rw_concat" $concatSQL "False"
    Add-Result "TC-RW-06" "Rewrite: string concatenation (CONCAT or + operator)" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
}

# RW-07: Self-JOIN (same table aliased twice)
$sql = "SELECT a.$keyCol, a.$strCol FROM $testTable a JOIN $testTable b ON a.$keyCol = b.$keyCol WHERE a.$keyCol IS NOT NULL LIMIT 5"
$r   = Run-DB "rw_selfjoin" $sql "False"
Add-Result "TC-RW-07" "Rewrite: self-JOIN — table aliased twice, ON clause correct" `
    $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"

# RW-08: TRIM / LTRIM / RTRIM
if($strCol){
    $trimSQL = if($dbEngine -eq "SQL Server"){
        "SELECT LTRIM(RTRIM($strCol)) AS Trimmed FROM $testTable LIMIT 5"
    } else {
        "SELECT TRIM($strCol) AS Trimmed FROM $testTable LIMIT 5"
    }
    $r = Run-DB "rw_trim" $trimSQL "False"
    Add-Result "TC-RW-08" "Rewrite: TRIM function" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) ""
}

# RW-09: BETWEEN on string
if($strCol){
    $sql = "SELECT * FROM $testTable WHERE $strCol BETWEEN 'A' AND 'Z'"
    $r   = Run-DB "rw_str_between" $sql "False"
    Add-Result "TC-RW-09" "Rewrite: BETWEEN on string column" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
}

# RW-10: NULL in arithmetic — result should be NULL (not error)
if($numCol){
    $sql = "SELECT $numCol + NULL AS Result FROM $testTable LIMIT 3"
    $r   = Run-DB "rw_null_arith" $sql "False"
    Add-Result "TC-RW-10" "Rewrite: NULL in arithmetic (col + NULL) — no SQL error" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) ""
}
```

---

## Phase S8 — Edge cases (with cross-mode comparison)

```powershell
$edgeCases = @(
    @{ tag="always_true";  sql="SELECT * FROM $testTable WHERE 1=1";                      desc="WHERE 1=1" }
    @{ tag="always_false"; sql="SELECT * FROM $testTable WHERE 1=0";                      desc="WHERE 1=0" }
    @{ tag="zero_rows";    sql="SELECT * FROM $testTable WHERE $keyCol = -99999";         desc="Filter matching nothing" }
)
foreach($c in $edgeCases){ Compare-Modes $c.tag $c.sql $c.desc }

# Bad column — expect SQL error in both modes
$r = Run-DB "bad_col" "SELECT NONEXISTENT_COLUMN_XYZ FROM $testTable" "False"
Add-Result "TC-SEL-BAD-COL" "SELECT non-existent column — expect SQL error (QPT=False)" `
    $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
```


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qptfalse select" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
