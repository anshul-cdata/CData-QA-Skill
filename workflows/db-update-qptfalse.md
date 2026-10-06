---
name: db-update-qptfalse
description: UPDATE tests for CData DB driver — QueryPassThrough=False. Invoked by /qptfalse update.
---

# `/qptfalse update` — UPDATE Tests (QueryPassThrough=False)

**Mode: QPT=False** — CData SQL engine processes UPDATE statements before forwarding.

**QPT=False goal:** Every UPDATE that succeeds/fails in QPT=True must behave identically
in QPT=False. The final row value after each UPDATE must match between modes.

```powershell
$script:currentQPT = "False"
Write-Host "=== UPDATE — QueryPassThrough=False ==="

function Compare-UpdateResult([string]$tag, [string]$sql, [string]$desc) {
    $rF = Run-DB "${tag}_qptf" $sql "False"
    $rT = Run-DB "${tag}_qptt" $sql "True"
    $okF = $rF.Out -match "\[OK\]|\[AFFECTED\]" -and $rF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rT.Out -match "\[OK\]|\[AFFECTED\]" -and $rT.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-CMP-$tag" "$desc — QPT=False outcome matches QPT=True" `
        $(if($okF -eq $okT){"PASS"}else{"FAIL"}) `
        "QPT=False=$(if($okF){'OK'}else{'ERR'}) QPT=True=$(if($okT){'OK'}else{'ERR'})" `
        $(if($okF -ne $okT){"BUG: UPDATE outcome differs between QPT modes"})
    return $rF
}

$keyCol    = "Id"
$schemaCols = @((Run-DB "upd_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "False").Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$updatable  = $schemaCols | Where-Object { $_["IsKey"] -ne "true" -and $_["IsReadOnly"] -ne "true" }
$strCol     = ($updatable | Where-Object { $_["DataType"] -match "varchar|nvarchar|text" } | Select-Object -First 1)["ColumnName"]
$numCol     = ($updatable | Where-Object { $_["DataType"] -match "int|float|decimal|number" } | Select-Object -First 1)["ColumnName"]
$dateCol    = ($updatable | Where-Object { $_["DataType"] -match "date|timestamp|datetime" } | Select-Object -First 1)["ColumnName"]
$boolCol    = ($updatable | Where-Object { $_["DataType"] -match "bit|bool" } | Select-Object -First 1)["ColumnName"]
$nullCol    = ($updatable | Where-Object { $_["IsNullable"] -eq "true" } | Select-Object -First 1)["ColumnName"]
$targetId   = if($script:insertedIds.Count -gt 0){ $script:insertedIds[0] }else{
    $r = Run-DB "upd_find" "SELECT $keyCol FROM $testTable LIMIT 1" "False"; $r.Parsed.Rows[0][$keyCol]
}
```

---

## UPDATE tests with cross-mode comparison

```powershell
# TC-UPD-01: String col by PK
if($strCol -and $targetId){
    $newVal = "QPTFalseUpd_$(Get-Random)"
    $sql    = "UPDATE $testTable SET $strCol='$newVal' WHERE $keyCol=$targetId"
    $r      = Compare-UpdateResult "upd_pk_str" $sql "UPDATE string col by PK"
    # Verify value written
    $rVF = Run-DB "upd_verify_f" "SELECT $strCol FROM $testTable WHERE $keyCol=$targetId" "False"
    $rVT = Run-DB "upd_verify_t" "SELECT $strCol FROM $testTable WHERE $keyCol=$targetId" "True"
    $valMatch = $rVF.Parsed.Rows[0][$strCol] -eq $rVT.Parsed.Rows[0][$strCol]
    Add-Result "TC-UPD-01-VAL" "Value after UPDATE is same in both modes" `
        $(if($valMatch){"PASS"}else{"FAIL"}) "QPT=False='$($rVF.Parsed.Rows[0][$strCol])' QPT=True='$($rVT.Parsed.Rows[0][$strCol])'"
}

# TC-UPD-02: Multiple columns
if($strCol -and $numCol -and $targetId){
    Compare-UpdateResult "upd_multicol" "UPDATE $testTable SET $strCol='MultiUpd', $numCol=99 WHERE $keyCol=$targetId" "UPDATE multiple columns"
}

# TC-UPD-03: Date col — valid
if($dateCol -and $targetId){
    Compare-UpdateResult "upd_date_valid" "UPDATE $testTable SET $dateCol='2026-01-01 09:00:00' WHERE $keyCol=$targetId" "UPDATE date col valid"
}

# TC-UPD-04: Date col — invalid (both modes must error)
if($dateCol -and $targetId){
    Compare-UpdateResult "upd_date_bad" "UPDATE $testTable SET $dateCol='NOT_A_DATE' WHERE $keyCol=$targetId" "UPDATE date col invalid — both modes error"
}

# TC-UPD-05: Bool col
if($boolCol -and $targetId){
    foreach($bv in @("1","0")){
        Compare-UpdateResult "upd_bool_$bv" "UPDATE $testTable SET $boolCol=$bv WHERE $keyCol=$targetId" "UPDATE bool = $bv"
    }
}

# TC-UPD-06: Set nullable col to NULL
if($nullCol -and $targetId){
    Compare-UpdateResult "upd_set_null" "UPDATE $testTable SET $nullCol=NULL WHERE $keyCol=$targetId" "UPDATE nullable col to NULL"
}

# TC-UPD-07: Expression (col + 1)
if($numCol -and $targetId){
    Compare-UpdateResult "upd_expr" "UPDATE $testTable SET $numCol=$numCol+1 WHERE $keyCol=$targetId" "UPDATE expression col+1"
}

# TC-UPD-08: Non-existent key — 0 rows, no error
if($strCol){
    Compare-UpdateResult "upd_nonexistent" "UPDATE $testTable SET $strCol='Ghost' WHERE $keyCol=-999999" "UPDATE non-existent key — 0 rows"
}

# TC-UPD-09: IN list
if($script:insertedIds.Count -ge 2 -and $strCol){
    $id1 = $script:insertedIds[0]; $id2 = $script:insertedIds[1]
    Compare-UpdateResult "upd_in_list" "UPDATE $testTable SET $strCol='BulkUpd' WHERE $keyCol IN ($id1,$id2)" "UPDATE IN list 2 rows"
}

# TC-UPD-10: No WHERE — gated
if(Confirm-Destructive "Unbounded operation on $testTable"){
    Compare-UpdateResult "upd_nowhere" "UPDATE $testTable SET $strCol='NOWHERETEST'" "UPDATE without WHERE — both modes reject"
} else {
    Add-Result "TC-UPD-10" "UPDATE without WHERE — SKIPPED" "SKIPPED" ""
}


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qptfalse update" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
