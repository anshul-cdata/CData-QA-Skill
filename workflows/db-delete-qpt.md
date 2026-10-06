---
name: db-delete-qpt
description: DELETE tests for CData DB driver — QueryPassThrough=True. Invoked by /qpttrue delete.
---

# `/qpttrue delete` — DELETE Tests (QueryPassThrough=True)

**Mode: QPT=True** — DELETE statements forwarded verbatim to DB engine.

```powershell
$script:currentQPT = "True"
Write-Host "=== DELETE — QueryPassThrough=True ==="

function Assert-QPTPassthrough([hashtable]$result, [string]$prefix) {
    $sentLine = $result.Log.SentSQL | Select-Object -First 1
    if(-not $sentLine){ Add-Result "$($result.Tag)-QPT" "DELETE forwarded verbatim" "INFO" "No SentSQL in log"; return }
    $p = $prefix.Trim().Substring(0,[Math]::Min(40,$prefix.Trim().Length))
    $m = $sentLine -match [regex]::Escape($p)
    Add-Result "$($result.Tag)-QPT" "DELETE forwarded verbatim to server" `
        $(if($m){"PASS"}else{"FAIL"}) "Prefix: '$p' | Log: $sentLine" `
        $(if(-not $m){"BUG: Driver rewrote DELETE — QPT=True must not rewrite"})
}

$keyCol  = "Id"
$schemaCols = @((Run-DB "del_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "True").Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$strCol  = ($schemaCols | Where-Object { $_["DataType"] -match "varchar|nvarchar|text" } | Select-Object -First 1)["ColumnName"]
$numCol  = ($schemaCols | Where-Object { $_["DataType"] -match "int|float|decimal" } | Select-Object -First 1)["ColumnName"]

function Insert-FreshRow {
    $colNames = ($schemaCols | Where-Object { $_["ColumnName"] -ne $keyCol } | Select-Object -First 3 | ForEach-Object { $_["ColumnName"] }) -join ","
    $colVals  = "'DelTarget_$(Get-Random)','DelTest','0'"
    $rIns = Run-DB "del_fresh_ins" "INSERT INTO $testTable ($strCol) VALUES ('DelTarget_$(Get-Random)')" "True"
    if($rIns.Out -match "\[OK\]|\[AFFECTED\]"){
        $rSel = Run-DB "del_fresh_id" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" "True"
        if($rSel.Parsed.Rows.Count -gt 0){ return $rSel.Parsed.Rows[0][$keyCol] }
    }
    return $null
}
```

---

## DELETE test cases

```powershell
# TC-DEL-01: Valid DELETE by PK
$delId = Insert-FreshRow
if($delId){
    $sql = "DELETE FROM $testTable WHERE $keyCol=$delId"
    $r   = Run-DB "del_by_pk" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-DEL-01" "DELETE by primary key" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql

    # TC-DEL-01a: SELECT after DELETE — row gone
    Start-Sleep -Milliseconds 300
    $rSel = Run-DB "del_verify" "SELECT * FROM $testTable WHERE $keyCol=$delId" "True"
    Add-Result "TC-DEL-01a" "SELECT after DELETE — row gone" `
        $(if($rSel.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) "Remaining=$($rSel.Parsed.Count)"
    Assert-QPTPassthrough $rSel "SELECT * FROM $testTable"
}

# TC-DEL-02: Non-existent key — 0 rows, no error
$sql = "DELETE FROM $testTable WHERE $keyCol=-999999"
$r   = Run-DB "del_nonexistent" $sql "True"
Add-Result "TC-DEL-02" "DELETE non-existent key — 0 rows, no error" `
    $(if(($r.Out -match "\[OK\]|\[AFFECTED\] 0") -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
Assert-QPTPassthrough $r $sql

# TC-DEL-03: DELETE by non-key filter
if($strCol){
    $filterVal = "DelFilter_$(Get-Random)"
    $rIns = Run-DB "del_filter_ins" "INSERT INTO $testTable ($strCol) VALUES ('$filterVal')" "True"
    if($rIns.Out -match "\[OK\]|\[AFFECTED\]"){
        $sql = "DELETE FROM $testTable WHERE $strCol='$filterVal'"
        $r   = Run-DB "del_by_filter" $sql "True"
        $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
        Add-Result "TC-DEL-03" "DELETE by non-key filter (string col)" $(if($ok){"PASS"}else{"FAIL"}) ""
        Assert-QPTPassthrough $r $sql
        $rV = Run-DB "del_filter_v" "SELECT * FROM $testTable WHERE $strCol='$filterVal'" "True"
        Add-Result "TC-DEL-03a" "SELECT after filter DELETE — row gone" `
            $(if($rV.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) "Remaining=$($rV.Parsed.Count)"
    }
}

# TC-DEL-04: DELETE IN list — multiple rows
$freshIds = @()
for($i=0;$i -lt 3;$i++){ $fid = Insert-FreshRow; if($fid){ $freshIds += $fid } }
if($freshIds.Count -ge 2){
    $inList = $freshIds -join ","
    $sql = "DELETE FROM $testTable WHERE $keyCol IN ($inList)"
    $r   = Run-DB "del_in_list" $sql "True"
    $ok  = $r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-DEL-04" "DELETE IN list ($($freshIds.Count) rows)" $(if($ok){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r $sql
    $rV = Run-DB "del_in_v" "SELECT * FROM $testTable WHERE $keyCol IN ($inList)" "True"
    Add-Result "TC-DEL-04a" "SELECT after IN DELETE — rows gone" `
        $(if($rV.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) "Remaining=$($rV.Parsed.Count)"
}

# TC-DEL-05: DELETE via subquery
$subId = Insert-FreshRow
if($subId -and $numCol){
    $sql = "DELETE FROM $testTable WHERE $keyCol = (SELECT MIN($keyCol) FROM $testTable WHERE $keyCol=$subId)"
    $r   = Run-DB "del_subquery" $sql "True"
    Add-Result "TC-DEL-05" "DELETE via subquery in WHERE" `
        $(if($r.Out -match "\[OK\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) ""
    Assert-QPTPassthrough $r "DELETE FROM $testTable"
}

# TC-DEL-06: DELETE without WHERE — gated
if(Confirm-Destructive "Unbounded operation on $testTable"){
    $r = Run-DB "del_nowhere" "DELETE FROM $testTable" "True"
    Add-Result "TC-DEL-06" "DELETE without WHERE — expect rejection" `
        $(if($r.Out -match "\[SQL_ERR\]"){"PASS"}else{"FAIL"}) "" `
        $(if($r.Out -notmatch "\[SQL_ERR\]"){"CRITICAL BUG: Unbounded DELETE"})
} else {
    Add-Result "TC-DEL-06" "DELETE without WHERE — SKIPPED" "SKIPPED" ""
}

# Final cleanup + report
Cleanup-TestTable
```


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qpttrue delete" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
