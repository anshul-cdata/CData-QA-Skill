---
name: db-select-qpt
description: SELECT tests for CData DB driver with QueryPassThrough=True. Invoked by /qpttrue select or as part of /qpt full suite.
---

# `/qpttrue select` — SELECT Tests (QueryPassThrough=True)

**Mode: QPT=True** — every SQL query is forwarded verbatim to the database engine.

Goals for this mode:
1. SQL sent to server **exactly matches** what was written — no rewriting by CData.
2. All SQL constructs execute correctly on the target DB engine.
3. Result set columns and types match what the DB natively returns.
4. Row counts are accurate.
5. Server errors surface as clear SQLExceptions — none swallowed.

After every test, run the **QPT passthrough check**:

```powershell
function Assert-QPTPassthrough([hashtable]$result, [string]$expectedSQLPrefix) {
    $sentLine = $result.Log.SentSQL | Select-Object -First 1
    if (-not $sentLine) {
        Add-Result "$($result.Tag)-QPT" "SQL forwarded verbatim to server" "INFO" `
            "No 'Executing SQL' line in log — increase Verbosity or check log path"
        return
    }
    $prefix  = $expectedSQLPrefix.Trim().Substring(0, [Math]::Min(40, $expectedSQLPrefix.Trim().Length))
    $matches = $sentLine -match [regex]::Escape($prefix)
    Add-Result "$($result.Tag)-QPT" "SQL forwarded verbatim to server" `
        $(if($matches){"PASS"}else{"FAIL"}) `
        "Expected prefix: '$prefix' | Log: $sentLine" `
        $(if(-not $matches){"BUG: Driver rewrote or truncated SQL before forwarding — QueryPassThrough=True should never rewrite"})
}
```

Set QPT mode:
```powershell
$script:currentQPT = "True"
Write-Host "=== SELECT — QueryPassThrough=True ==="
```

---

## Phase S0 — Baseline

```powershell
Write-Host "`n=== SELECT TESTS (QPT=True): $testTable ==="

$rBase      = Run-DB "sel_baseline" "SELECT * FROM $testTable" "True"
$baseRows   = $rBase.Parsed.Rows
$baseCnt    = $rBase.Parsed.Count
$schemaCols = $rBase.Parsed.Cols
$schemaTypes = $rBase.Parsed.Types

Add-Result "TC-SEL-00" "Baseline SELECT * — table has rows" `
    $(if($baseCnt -gt 0){"PASS"}else{"FAIL"}) "Rows=$baseCnt Cols=$($schemaCols.Count)"
Assert-QPTPassthrough $rBase "SELECT * FROM $testTable"

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

## Phase S1 — Projection

```powershell
# TC-SEL-01: SELECT two specific columns
$sql = "SELECT $keyCol, $strCol FROM $testTable"
$r   = Run-DB "sel_proj_2col" $sql "True"
Add-Result "TC-SEL-01" "Projection: SELECT two specific columns" `
    $(if($r.Parsed.Cols.Count -eq 2){"PASS"}else{"FAIL"}) "ColsReturned=$($r.Parsed.Cols.Count)"
Assert-QPTPassthrough $r $sql

# TC-SEL-02: Column alias
$sql = "SELECT $keyCol AS PrimaryKey, $strCol AS Label FROM $testTable LIMIT 3"
$r   = Run-DB "sel_alias" $sql "True"
$ok  = $r.Parsed.Cols -contains "PrimaryKey" -or $r.Parsed.Cols -contains "primarykey"
Add-Result "TC-SEL-02" "Projection: column alias (AS)" `
    $(if($ok){"PASS"}else{"INFO"}) "Cols=$($r.Parsed.Cols -join ',')"
Assert-QPTPassthrough $r $sql

# TC-SEL-03: Arithmetic expression
if($numCol){
    $sql = "SELECT $numCol, $numCol * 2 AS Doubled FROM $testTable LIMIT 5"
    $r   = Run-DB "sel_expr" $sql "True"
    Add-Result "TC-SEL-03" "Projection: arithmetic expression (* 2)" `
        $(if($r.Parsed.Cols.Count -ge 2){"PASS"}else{"INFO"}) ""
    Assert-QPTPassthrough $r $sql
}

# TC-SEL-04: COUNT(*)
$sql = "SELECT COUNT(*) AS Total FROM $testTable"
$r   = Run-DB "sel_count_star" $sql "True"
Add-Result "TC-SEL-04" "Aggregate: COUNT(*)" `
    $(if($r.Parsed.Rows[0]["Total"] -gt 0){"PASS"}else{"FAIL"}) "Total=$($r.Parsed.Rows[0]["Total"])"
Assert-QPTPassthrough $r $sql

# TC-SEL-05: DISTINCT
if($strCol){
    $sql = "SELECT DISTINCT $strCol FROM $testTable"
    $r   = Run-DB "sel_distinct" $sql "True"
    Add-Result "TC-SEL-05" "DISTINCT" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
    Assert-QPTPassthrough $r $sql
}
```

---

## Phase S2 — WHERE operators

```powershell
# --- String operators ---
if($strCol){
    $cases = @(
        @{ tag="eq_str";     sql="SELECT * FROM $testTable WHERE $strCol = '$strVal'";                              desc="WHERE string =" }
        @{ tag="neq_str";    sql="SELECT * FROM $testTable WHERE $strCol != '$strVal'";                             desc="WHERE string !=" }
        @{ tag="in_str";     sql="SELECT * FROM $testTable WHERE $strCol IN ('$strVal','NONEXISTENT_XYZ')";         desc="WHERE string IN" }
        @{ tag="notin_str";  sql="SELECT * FROM $testTable WHERE $strCol NOT IN ('NONEXISTENT_XYZ')";               desc="WHERE string NOT IN" }
        @{ tag="like_pre";   sql="SELECT * FROM $testTable WHERE $strCol LIKE '$($strVal.Substring(0,[Math]::Min(3,$strVal.Length)))%'"; desc="WHERE LIKE prefix%" }
        @{ tag="like_con";   sql="SELECT * FROM $testTable WHERE $strCol LIKE '%$($strVal.Substring(0,[Math]::Min(3,$strVal.Length)))%'"; desc="WHERE LIKE %contains%" }
        @{ tag="isnull";     sql="SELECT * FROM $testTable WHERE NullableVarchar IS NULL";                          desc="WHERE IS NULL" }
        @{ tag="isnotnull";  sql="SELECT * FROM $testTable WHERE NullableVarchar IS NOT NULL";                      desc="WHERE IS NOT NULL" }
    )
    $i = 10
    foreach($c in $cases){
        $r = Run-DB $c.tag $c.sql "True"
        Add-Result "TC-SEL-$i" $c.desc `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $c.sql
        $i++
    }
}

# --- Numeric operators ---
if($numCol){
    $numCases = @(
        @{ tag="num_eq";    sql="SELECT * FROM $testTable WHERE $numCol = $numVal";                desc="WHERE numeric =" }
        @{ tag="num_neq";   sql="SELECT * FROM $testTable WHERE $numCol != $numVal";               desc="WHERE numeric !=" }
        @{ tag="num_gt";    sql="SELECT * FROM $testTable WHERE $numCol > 0";                      desc="WHERE numeric >" }
        @{ tag="num_gte";   sql="SELECT * FROM $testTable WHERE $numCol >= 0";                     desc="WHERE numeric >=" }
        @{ tag="num_lt";    sql="SELECT * FROM $testTable WHERE $numCol < 999999";                 desc="WHERE numeric <" }
        @{ tag="num_lte";   sql="SELECT * FROM $testTable WHERE $numCol <= 999999";                desc="WHERE numeric <=" }
        @{ tag="num_btwn";  sql="SELECT * FROM $testTable WHERE $numCol BETWEEN 0 AND 999999";     desc="WHERE numeric BETWEEN" }
        @{ tag="num_in";    sql="SELECT * FROM $testTable WHERE $numCol IN (0,$numVal,999999)";    desc="WHERE numeric IN" }
    )
    $i = 20
    foreach($c in $numCases){
        $r = Run-DB $c.tag $c.sql "True"
        Add-Result "TC-SEL-$i" $c.desc `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $c.sql
        $i++
    }
}

# --- Date operators ---
if($dateCol){
    $d1 = "2020-01-01"; $d2 = "2030-12-31"
    $dateCases = @(
        @{ tag="date_eq";    sql="SELECT * FROM $testTable WHERE $dateCol = '$dateVal'";              desc="WHERE date =" }
        @{ tag="date_gt";    sql="SELECT * FROM $testTable WHERE $dateCol > '$d1'";                  desc="WHERE date >" }
        @{ tag="date_gte";   sql="SELECT * FROM $testTable WHERE $dateCol >= '$d1'";                 desc="WHERE date >=" }
        @{ tag="date_lt";    sql="SELECT * FROM $testTable WHERE $dateCol < '$d2'";                  desc="WHERE date <" }
        @{ tag="date_lte";   sql="SELECT * FROM $testTable WHERE $dateCol <= '$d2'";                 desc="WHERE date <=" }
        @{ tag="date_btwn";  sql="SELECT * FROM $testTable WHERE $dateCol BETWEEN '$d1' AND '$d2'";  desc="WHERE date BETWEEN" }
    )
    $i = 30
    foreach($c in $dateCases){
        $r = Run-DB $c.tag $c.sql "True"
        Add-Result "TC-SEL-$i" $c.desc `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $c.sql
        $i++
    }
}

# --- Boolean operators ---
if($boolCol){
    foreach($bv in @("1","0")){
        $sql = "SELECT * FROM $testTable WHERE $boolCol = $bv"
        $r   = Run-DB "bool_$bv" $sql "True"
        Add-Result "TC-SEL-4$(if($bv -eq '1'){'0'}else{'1'})" "WHERE bool = $bv" `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $sql
    }
}

# --- Compound WHERE ---
if($strCol -and $numCol){
    $compoundCases = @(
        @{ tag="and";      sql="SELECT * FROM $testTable WHERE $strCol IS NOT NULL AND $numCol >= 0";                                    desc="WHERE AND" }
        @{ tag="or";       sql="SELECT * FROM $testTable WHERE $strCol = '$strVal' OR $numCol > 999999";                                 desc="WHERE OR" }
        @{ tag="and_or";   sql="SELECT * FROM $testTable WHERE ($strCol IS NOT NULL) AND ($numCol > 0 OR $numCol = 0)";                  desc="WHERE (AND) with (OR)" }
        @{ tag="not";      sql="SELECT * FROM $testTable WHERE NOT ($strCol = 'NONEXISTENT_XYZ')";                                      desc="WHERE NOT" }
    )
    $i = 50
    foreach($c in $compoundCases){
        $r = Run-DB $c.tag $c.sql "True"
        Add-Result "TC-SEL-$i" $c.desc `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $c.sql
        $i++
    }
}
```

---

## Phase S3 — ORDER BY

```powershell
if($numCol){
    # ASC
    $sql = "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol ASC"
    $r   = Run-DB "order_asc" $sql "True"
    $rows = $r.Parsed.Rows
    $sorted = $true
    for($i=1;$i -lt $rows.Count;$i++){ if([double]$rows[$i][$numCol] -lt [double]$rows[$i-1][$numCol]){$sorted=$false;break} }
    Add-Result "TC-SEL-60" "ORDER BY numeric ASC — rows are ascending" `
        $(if($sorted){"PASS"}else{"FAIL"}) "Sorted=$sorted"
    Assert-QPTPassthrough $r $sql

    # DESC
    $sql = "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol DESC"
    $r   = Run-DB "order_desc" $sql "True"
    $rows = $r.Parsed.Rows
    $sorted = $true
    for($i=1;$i -lt $rows.Count;$i++){ if([double]$rows[$i][$numCol] -gt [double]$rows[$i-1][$numCol]){$sorted=$false;break} }
    Add-Result "TC-SEL-61" "ORDER BY numeric DESC — rows are descending" `
        $(if($sorted){"PASS"}else{"FAIL"}) "Sorted=$sorted"
    Assert-QPTPassthrough $r $sql
}

if($strCol){
    $sql = "SELECT $keyCol, $strCol FROM $testTable ORDER BY $strCol ASC"
    $r   = Run-DB "order_str" $sql "True"
    Add-Result "TC-SEL-62" "ORDER BY string column ASC" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

if($strCol -and $numCol){
    $sql = "SELECT $keyCol, $strCol, $numCol FROM $testTable ORDER BY $strCol ASC, $numCol DESC"
    $r   = Run-DB "order_multi" $sql "True"
    Add-Result "TC-SEL-63" "ORDER BY two columns" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

# ORDER BY + LIMIT consistency
if($numCol){
    $sqlAll   = "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol ASC"
    $sqlLimit = "SELECT $keyCol, $numCol FROM $testTable ORDER BY $numCol ASC LIMIT 3"
    $rAll     = Run-DB "order_all"   $sqlAll   "True"
    $rLim     = Run-DB "order_limit" $sqlLimit "True"
    $match    = $true
    for($i=0;$i -lt [Math]::Min(3,$rAll.Parsed.Rows.Count);$i++){
        if($rAll.Parsed.Rows[$i][$keyCol] -ne $rLim.Parsed.Rows[$i][$keyCol]){ $match=$false; break }
    }
    Add-Result "TC-SEL-64" "ORDER BY + LIMIT: first 3 rows consistent" `
        $(if($match){"PASS"}else{"FAIL"}) "Match=$match"
    Assert-QPTPassthrough $rLim $sqlLimit
}
```

---

## Phase S4 — LIMIT and OFFSET

```powershell
foreach($lim in @(1,3,5)){
    $sql = "SELECT * FROM $testTable LIMIT $lim"
    $r   = Run-DB "limit_$lim" $sql "True"
    Add-Result "TC-SEL-7$lim" "LIMIT $lim" `
        $(if($r.Parsed.Count -le $lim){"PASS"}else{"FAIL"}) "Returned=$($r.Parsed.Count) Max=$lim" `
        $(if($r.Parsed.Count -gt $lim){"BUG: LIMIT $lim not honoured"})
    Assert-QPTPassthrough $r $sql
}

# OFFSET pagination — no overlap between pages
if($baseCnt -gt 2){
    $sql1 = "SELECT $keyCol FROM $testTable ORDER BY $keyCol LIMIT 2 OFFSET 0"
    $sql2 = "SELECT $keyCol FROM $testTable ORDER BY $keyCol LIMIT 2 OFFSET 2"
    $rP1  = Run-DB "offset_p1" $sql1 "True"
    $rP2  = Run-DB "offset_p2" $sql2 "True"
    $noOverlap = $true
    foreach($r1 in $rP1.Parsed.Rows){
        if($rP2.Parsed.Rows | Where-Object { $_[$keyCol] -eq $r1[$keyCol] }){ $noOverlap=$false }
    }
    Add-Result "TC-SEL-73" "LIMIT+OFFSET: page 1 and page 2 have no overlap" `
        $(if($noOverlap){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $rP1 $sql1
}

# LIMIT 0
$sql = "SELECT * FROM $testTable LIMIT 0"
$r   = Run-DB "limit_0" $sql "True"
Add-Result "TC-SEL-74" "LIMIT 0 — returns 0 rows" `
    $(if($r.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) "Returned=$($r.Parsed.Count)"
Assert-QPTPassthrough $r $sql
```

---

## Phase S5 — Aggregates and GROUP BY

```powershell
if($numCol){
    $aggCases = @(
        @{ fn="COUNT(*)";     alias="Cnt" }
        @{ fn="SUM($numCol)"; alias="Sum" }
        @{ fn="AVG($numCol)"; alias="Avg" }
        @{ fn="MIN($numCol)"; alias="Min" }
        @{ fn="MAX($numCol)"; alias="Max" }
    )
    $i = 80
    foreach($a in $aggCases){
        $sql = "SELECT $($a.fn) AS $($a.alias) FROM $testTable"
        $r   = Run-DB "agg_$($a.alias)" $sql "True"
        Add-Result "TC-SEL-$i" "Aggregate: $($a.fn)" `
            $(if($r.Parsed.Rows[0][$a.alias] -ne $null -and -not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) `
            "Result=$($r.Parsed.Rows[0][$a.alias])"
        Assert-QPTPassthrough $r $sql
        $i++
    }
}

if($strCol -and $numCol){
    $sql = "SELECT $strCol, COUNT(*) AS Cnt, SUM($numCol) AS Total FROM $testTable GROUP BY $strCol"
    $r   = Run-DB "groupby" $sql "True"
    Add-Result "TC-SEL-85" "GROUP BY + COUNT + SUM" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Groups=$($r.Parsed.Count)"
    Assert-QPTPassthrough $r $sql

    $sql = "SELECT $strCol, COUNT(*) AS Cnt FROM $testTable GROUP BY $strCol HAVING COUNT(*) >= 1"
    $r   = Run-DB "having" $sql "True"
    Add-Result "TC-SEL-86" "GROUP BY + HAVING" `
        $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Groups=$($r.Parsed.Count)"
    Assert-QPTPassthrough $r $sql
}
```

---

## Phase S6 — Subqueries

```powershell
if($numCol){
    $sqCases = @(
        @{ tag="subq_where"; sql="SELECT * FROM $testTable WHERE $numCol = (SELECT MAX($numCol) FROM $testTable)";                                          desc="Subquery in WHERE" }
        @{ tag="subq_from";  sql="SELECT sub.$keyCol, sub.$numCol FROM (SELECT $keyCol, $numCol FROM $testTable WHERE $numCol >= 0) AS sub";               desc="Derived table in FROM" }
        @{ tag="subq_exists";sql="SELECT * FROM $testTable t WHERE EXISTS (SELECT 1 FROM $testTable t2 WHERE t2.$keyCol = t.$keyCol)";                     desc="EXISTS subquery" }
    )
    $i = 90
    foreach($c in $sqCases){
        $r = Run-DB $c.tag $c.sql "True"
        Add-Result "TC-SEL-$i" $c.desc `
            $(if(-not ($r.Out -match "\[SQL_ERR\]")){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
        Assert-QPTPassthrough $r $c.sql
        $i++
    }
}
```

---

## Phase S7 — NULL edge cases and edge queries

```powershell
$edgeCases = @(
    @{ tag="null_eq";      sql="SELECT * FROM $testTable WHERE NullableVarchar = NULL";      desc="WHERE col = NULL (should return 0 rows)" }
    @{ tag="always_true";  sql="SELECT * FROM $testTable WHERE 1=1";                         desc="WHERE 1=1 — returns all rows" }
    @{ tag="always_false"; sql="SELECT * FROM $testTable WHERE 1=0";                         desc="WHERE 1=0 — returns 0 rows" }
    @{ tag="bad_col";      sql="SELECT NONEXISTENT_COLUMN_XYZ FROM $testTable";              desc="SELECT non-existent column — expect SQL error" }
    @{ tag="zero_rows";    sql="SELECT * FROM $testTable WHERE $keyCol = -99999";            desc="Filter matching nothing — empty result set" }
)
$i = 95
foreach($c in $edgeCases){
    $r = Run-DB $c.tag $c.sql "True"
    $pass = switch($c.tag){
        "null_eq"      { $r.Parsed.Count -eq 0 -and -not ($r.Out -match "\[SQL_ERR\]") }
        "always_true"  { $r.Parsed.Count -eq $baseCnt }
        "always_false" { $r.Parsed.Count -eq 0 -and -not ($r.Out -match "\[SQL_ERR\]") }
        "bad_col"      { $r.Out -match "\[SQL_ERR\]" }
        "zero_rows"    { $r.Parsed.Count -eq 0 -and -not ($r.Out -match "\[SQL_ERR\]") }
        default        { $true }
    }
    Add-Result "TC-SEL-$i" $c.desc `
        $(if($pass){"PASS"}else{"FAIL"}) "Rows=$($r.Parsed.Count)"
    if($c.tag -ne "bad_col"){ Assert-QPTPassthrough $r $c.sql }
    $i++
}
```


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qpttrue select" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
