---
name: db-delete-qptfalse
description: DELETE tests for CData DB driver — QueryPassThrough=False. Invoked by /qptfalse delete.
---

# `/qptfalse delete` — DELETE Tests (QueryPassThrough=False)

**Mode: QPT=False** — CData SQL engine processes DELETE statements before forwarding.

```powershell
$script:currentQPT = "False"
Write-Host "=== DELETE — QueryPassThrough=False ==="

function Compare-DeleteResult([string]$tag, [string]$sql, [string]$desc) {
    $rF = Run-DB "${tag}_qptf" $sql "False"
    $rT = Run-DB "${tag}_qptt" $sql "True"
    $okF = $rF.Out -match "\[OK\]|\[AFFECTED\]" -and $rF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rT.Out -match "\[OK\]|\[AFFECTED\]" -and $rT.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-CMP-$tag" "$desc — QPT=False matches QPT=True" `
        $(if($okF -eq $okT){"PASS"}else{"FAIL"}) `
        "QPT=False=$(if($okF){'OK'}else{'ERR'}) QPT=True=$(if($okT){'OK'}else{'ERR'})" `
        $(if($okF -ne $okT){"BUG: DELETE outcome differs between QPT modes"})
    return $rF
}

$keyCol  = "Id"
$schemaCols = @((Run-DB "del_meta" "SELECT * FROM sys_tablecolumns WHERE $($tcMap.TableName)='$testTable'" "False").Parsed.Rows | ForEach-Object { Normalize-TCRow $_ $tcMap })
$strCol  = ($schemaCols | Where-Object { $_["DataType"] -match "varchar|nvarchar|text" } | Select-Object -First 1)["ColumnName"]

function Insert-FreshRow([string]$mode = "False") {
    $rIns = Run-DB "del_fresh_ins_$mode" "INSERT INTO $testTable ($strCol) VALUES ('DelTarget_$(Get-Random)')" $mode
    if($rIns.Out -match "\[OK\]|\[AFFECTED\]"){
        $rSel = Run-DB "del_fresh_id_$mode" "SELECT $keyCol FROM $testTable ORDER BY $keyCol DESC LIMIT 1" $mode
        if($rSel.Parsed.Rows.Count -gt 0){ return $rSel.Parsed.Rows[0][$keyCol] }
    }
    return $null
}
```

---

## DELETE tests with cross-mode comparison

```powershell
# TC-DEL-01: DELETE by PK — insert one row in each mode, delete in same mode, verify gone
$delIdF = Insert-FreshRow "False"
$delIdT = Insert-FreshRow "True"
if($delIdF -and $delIdT){
    $rDF = Run-DB "del_pk_f" "DELETE FROM $testTable WHERE $keyCol=$delIdF" "False"
    $rDT = Run-DB "del_pk_t" "DELETE FROM $testTable WHERE $keyCol=$delIdT" "True"
    $okF = $rDF.Out -match "\[OK\]|\[AFFECTED\]" -and $rDF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rDT.Out -match "\[OK\]|\[AFFECTED\]" -and $rDT.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-DEL-01" "DELETE by PK — both modes succeed" `
        $(if($okF -and $okT){"PASS"}else{"FAIL"}) "QPT=False=$(if($okF){'OK'}else{'FAIL'}) QPT=True=$(if($okT){'OK'}else{'FAIL'})"
    # Verify gone
    $rVF = Run-DB "del_verify_f" "SELECT * FROM $testTable WHERE $keyCol=$delIdF" "False"
    $rVT = Run-DB "del_verify_t" "SELECT * FROM $testTable WHERE $keyCol=$delIdT" "True"
    Add-Result "TC-DEL-01a" "Row gone after DELETE in both modes" `
        $(if($rVF.Parsed.Count -eq 0 -and $rVT.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) `
        "QPT=False remaining=$($rVF.Parsed.Count) QPT=True remaining=$($rVT.Parsed.Count)"
}

# TC-DEL-02: Non-existent key — 0 rows, no error
Compare-DeleteResult "del_nonexistent" "DELETE FROM $testTable WHERE $keyCol=-999999" "DELETE non-existent key — 0 rows"

# TC-DEL-03: DELETE by non-key filter
if($strCol){
    $filterValF = "DelFilterF_$(Get-Random)"
    $filterValT = "DelFilterT_$(Get-Random)"
    Run-DB "del_filter_ins_f" "INSERT INTO $testTable ($strCol) VALUES ('$filterValF')" "False" | Out-Null
    Run-DB "del_filter_ins_t" "INSERT INTO $testTable ($strCol) VALUES ('$filterValT')" "True"  | Out-Null
    $rDF = Run-DB "del_filter_f" "DELETE FROM $testTable WHERE $strCol='$filterValF'" "False"
    $rDT = Run-DB "del_filter_t" "DELETE FROM $testTable WHERE $strCol='$filterValT'" "True"
    $okF = $rDF.Out -match "\[OK\]|\[AFFECTED\]" -and $rDF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rDT.Out -match "\[OK\]|\[AFFECTED\]" -and $rDT.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-DEL-03" "DELETE by string filter — both modes succeed" `
        $(if($okF -and $okT){"PASS"}else{"FAIL"}) ""
}

# TC-DEL-04: IN list
$freshIdsF = @(); $freshIdsT = @()
for($i=0;$i -lt 3;$i++){
    $fidF = Insert-FreshRow "False"; if($fidF){ $freshIdsF += $fidF }
    $fidT = Insert-FreshRow "True";  if($fidT){ $freshIdsT += $fidT }
}
if($freshIdsF.Count -ge 2 -and $freshIdsT.Count -ge 2){
    $inF = $freshIdsF -join ","; $inT = $freshIdsT -join ","
    $rDF = Run-DB "del_in_f" "DELETE FROM $testTable WHERE $keyCol IN ($inF)" "False"
    $rDT = Run-DB "del_in_t" "DELETE FROM $testTable WHERE $keyCol IN ($inT)" "True"
    $okF = $rDF.Out -match "\[OK\]|\[AFFECTED\]" -and $rDF.Out -notmatch "\[SQL_ERR\]"
    $okT = $rDT.Out -match "\[OK\]|\[AFFECTED\]" -and $rDT.Out -notmatch "\[SQL_ERR\]"
    Add-Result "TC-DEL-04" "DELETE IN list — both modes succeed" `
        $(if($okF -and $okT){"PASS"}else{"FAIL"}) ""
    $rVF = Run-DB "del_in_vf" "SELECT * FROM $testTable WHERE $keyCol IN ($inF)" "False"
    $rVT = Run-DB "del_in_vt" "SELECT * FROM $testTable WHERE $keyCol IN ($inT)" "True"
    Add-Result "TC-DEL-04a" "Rows gone after IN DELETE in both modes" `
        $(if($rVF.Parsed.Count -eq 0 -and $rVT.Parsed.Count -eq 0){"PASS"}else{"FAIL"}) `
        "QPT=False remaining=$($rVF.Parsed.Count) QPT=True remaining=$($rVT.Parsed.Count)"
}

# TC-DEL-05: No WHERE — gated
if(Confirm-Destructive "Unbounded operation on $testTable"){
    Compare-DeleteResult "del_nowhere" "DELETE FROM $testTable" "DELETE without WHERE — both modes reject"
} else {
    Add-Result "TC-DEL-05" "DELETE without WHERE — SKIPPED" "SKIPPED" ""
}

# Final cleanup + consolidated report
Cleanup-TestTable

Write-Host "`n═══ CONSOLIDATED REPORT ═══════════════════════════════════════════"
$script:results | Format-Table -AutoSize ID, Description, Verdict, Note
$p = ($script:results | Where-Object Verdict -eq "PASS").Count
$f = ($script:results | Where-Object Verdict -eq "FAIL").Count
$s = ($script:results | Where-Object Verdict -eq "SKIPPED").Count
$i = ($script:results | Where-Object Verdict -eq "INFO").Count
Write-Host "Summary: PASS=$p FAIL=$f SKIPPED=$s INFO=$i Total=$($script:results.Count)"
Write-Host "═══════════════════════════════════════════════════════════════════"


---

## Final step — record actual token usage

```powershell
# Record-Usage is defined in db-shared-setup.md and already loaded.
Record-Usage `
    -Command   "/qptfalse delete" `
    -Driver    "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table     $testTable `
    -Model     "claude-sonnet-4-6"
```
