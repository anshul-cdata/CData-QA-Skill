---
name: db-update-qpt
description: UPDATE tests for CData DB driver — QueryPassThrough=True. Invoked by /qpttrue update.
---

# `/qpttrue update` — UPDATE Tests (QueryPassThrough=True)

**Mode: QPT=True** — UPDATE statements forwarded verbatim to DB engine.

```powershell
$script:currentQPT = "True"
Write-Host "=== UPDATE — QueryPassThrough=True ==="

function Assert-QPTPassthrough([hashtable]$result, [string]$prefix) {
    $sentLine = $result.Log.SentSQL | Select-Object -First 1
    if(-not $sentLine){ Add-Result "$($result.Tag)-QPT" "UPDATE forwarded verbatim" "INFO" "No SentSQL in log"; return }
    $p = $prefix.Trim().Substring(0,[Math]::Min(40,$prefix.Trim().Length))
    $m = $sentLine -match [regex]::Escape($p)
    Add-Result "$($result.Tag)-QPT" "UPDATE forwarded verbatim to server" `
        $(if($m){"PASS"}else{"FAIL"}) "Prefix: '$p' | Log: $sentLine" `
        $(if(-not $m){"BUG: Driver rewrote UPDATE — QPT=True must not rewrite"})
}

$keyCol    = "Id"
$schemaCols = @((Run-DB "upd_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "True").Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$updatable  = $schemaCols | Where-Object { $_["IsKey"] -ne "true" -and $_["IsReadOnly"] -ne "true" }
$strCol     = ($updatable | Where-Object { $_["DataType"] -match "varchar|nvarchar|text" } | Select-Object -First 1)["ColumnName"]
$numCol     = ($updatable | Where-Object { $_["DataType"] -match "int|float|decimal|number" } | Select-Object -First 1)["ColumnName"]
$dateCol    = ($updatable | Where-Object { $_["DataType"] -match "date|timestamp|datetime" } | Select-Object -First 1)["ColumnName"]
$boolCol    = ($updatable | Where-Object { $_["DataType"] -match "bit|bool" } | Select-Object -First 1)["ColumnName"]
$nullCol    = ($updatable | Where-Object { $_["IsNullable"] -eq "true" } | Select-Object -First 1)["ColumnName"]
$targetId   = if($script:insertedIds.Count -gt 0){ $script:insertedIds[0] }else{
    $r = Run-DB "upd_find" "SELECT $keyCol FROM $testTable LIMIT 1" "True"; $r.Parsed.Rows[0][$keyCol]
}
```

---

## UPDATE test cases

```powershell
# TC-UPD-01: UPDATE string col by PK
if($strCol -and $targetId){
    $newVal = "Updated_$(Get-Random)"
    $sql = "UPDATE $testTable SET $strCol='$newVal' WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_pk_str" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-UPD-01" "UPDATE string col by PK" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
    if($ok){
        $rV = Run-DB "upd_verify_str" "SELECT $strCol FROM $testTable WHERE $keyCol=$targetId" "True"
        $got = $rV.Parsed.Rows[0][$strCol]
        Add-Result "TC-UPD-01a" "SELECT after UPDATE — value persisted" `
            $(if($got -eq $newVal){"PASS"}else{"FAIL"}) "Exp='$newVal' Got='$got'"
        Assert-QPTPassthrough $rV "SELECT $strCol"
    }
}

# TC-UPD-02: UPDATE multiple columns
if($strCol -and $numCol -and $targetId){
    $sql = "UPDATE $testTable SET $strCol='MultiUpdate', $numCol=99 WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_multicol" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-UPD-02" "UPDATE multiple columns" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
    if($ok){
        $rV = Run-DB "upd_multicol_v" "SELECT $strCol,$numCol FROM $testTable WHERE $keyCol=$targetId" "True"
        $s  = $rV.Parsed.Rows[0][$strCol] -eq "MultiUpdate"
        $n  = $rV.Parsed.Rows[0][$numCol] -eq "99"
        Add-Result "TC-UPD-02a" "Both values correct after multi-col UPDATE" `
            $(if($s -and $n){"PASS"}else{"FAIL"}) "StrOK=$s NumOK=$n"
    }
}

# TC-UPD-03: UPDATE date col — valid
if($dateCol -and $targetId){
    $sql = "UPDATE $testTable SET $dateCol='2026-01-01 09:00:00' WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_date_valid" $sql "True"
    Add-Result "TC-UPD-03" "UPDATE datetime col with valid value" `
        $(if($r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

# TC-UPD-04: UPDATE date col — invalid string
if($dateCol -and $targetId){
    $sql = "UPDATE $testTable SET $dateCol='NOT_A_DATE' WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_date_bad" $sql "True"
    Add-Result "TC-UPD-04" "UPDATE datetime col with invalid string — expect error" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"BUG: Driver accepted malformed date in UPDATE"})
}

# TC-UPD-05: UPDATE bool col
if($boolCol -and $targetId){
    foreach($bv in @("1","0")){
        $sql = "UPDATE $testTable SET $boolCol=$bv WHERE $keyCol=$targetId"
        $r   = Run-DB "upd_bool_$bv" $sql "True"
        Add-Result "TC-UPD-05-$bv" "UPDATE bool col = $bv" `
            $(if($r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
        Assert-QPTPassthrough $r $sql
    }
}

# TC-UPD-06: UPDATE nullable col to NULL
if($nullCol -and $targetId){
    $sql = "UPDATE $testTable SET $nullCol=NULL WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_set_null" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-UPD-06" "UPDATE nullable col to NULL" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

# TC-UPD-07: UPDATE expression (col + 1)
if($numCol -and $targetId){
    $sql = "UPDATE $testTable SET $numCol=$numCol+1 WHERE $keyCol=$targetId"
    $r   = Run-DB "upd_expr" $sql "True"
    Add-Result "TC-UPD-07" "UPDATE with expression: col = col + 1" `
        $(if($r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

# TC-UPD-08: UPDATE non-existent key — 0 rows, no error
$sql = "UPDATE $testTable SET $strCol='Ghost' WHERE $keyCol=-999999"
$r   = Run-DB "upd_nonexistent" $sql "True"
Add-Result "TC-UPD-08" "UPDATE non-existent key — 0 rows affected, no error" `
    $(if(($r.Out -match "\[AFFECTED\] 0|\[OK\]") -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
Assert-QPTPassthrough $r $sql

# TC-UPD-09: UPDATE IN list — multiple rows
if($script:insertedIds.Count -ge 2 -and $strCol){
    $id1 = $script:insertedIds[0]; $id2 = $script:insertedIds[1]
    $sql = "UPDATE $testTable SET $strCol='BulkUpdate' WHERE $keyCol IN ($id1,$id2)"
    $r   = Run-DB "upd_in_list" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-UPD-09" "UPDATE IN list — 2 rows" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
}

# TC-UPD-10: UPDATE without WHERE — gated
if(Confirm-Destructive "Unbounded operation on $testTable"){
    $sql = "UPDATE $testTable SET $strCol='NOWHERETEST'"
    $r   = Run-DB "upd_nowhere" $sql "True"
    Add-Result "TC-UPD-10" "UPDATE without WHERE — expect rejection" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"CRITICAL BUG: Unbounded UPDATE"})
} else {
    Add-Result "TC-UPD-10" "UPDATE without WHERE — SKIPPED (user must confirm at runtime)" "SKIPPED" ""
}


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qpttrue update" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
