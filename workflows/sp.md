---
name: stored-procedure-validation
description: >
  Full stored procedure validation for CData JDBC API drivers. Invoked by /sp.
  Covers: dual SP discovery via RSB files AND sys_procedureparameters metadata (cross-matched),
  RSB vs API docs validation, required-param client-side enforcement (empty/null/whitespace
  must never reach the server), comprehensive negative test cases per data type (string→int,
  int→bool, date→string, file extensions, enums, URLs, etc.), positive permutation testing,
  driver behaviour checks, token tracking, and a full report with Jira ticket drafts.
---

# Stored Procedure Validation (`/sp`)

---

## Phase 0 — Gather inputs

| Input | What to ask for |
|---|---|
| **Connection string** | `jdbc:cdata:<driver>://<auth_properties>` |
| **JAR folder path** | Must contain `.jar` + `.lic` |
| **RSB folder path** | Folder containing `.rsb` files for stored procedures |
| **SP name(s) to test** | One or more SP names, or "all" to list and pick |

Verify JAR folder:
```powershell
ls "<jar_folder>"
# Confirm: exactly one cdata.jdbc.*.jar and one *.lic
```
Stop and warn if `.lic` is missing.

---

## Phase 1 — Connection + harness

**Step 1a — Discover driver class:**
```powershell
jar tf "$jar" | Select-String "Driver\.class" | Select-Object -First 1
# Convert: cdata/jdbc/square/SquareDriver.class → cdata.jdbc.square.SquareDriver
```

**Step 1b — Compile harness (BOM-safe):**
```powershell
$sp = "$env:TEMP\cdata_sp_$(Get-Random)"
New-Item -ItemType Directory $sp -Force | Out-Null

$src = @"
import java.sql.*;
public class SPProbe {
    public static void main(String[] args) throws Exception {
        Class.forName(args[0]);
        String url = args[1];
        String sql = args.length > 2 ? args[2] : null;
        try (Connection c = DriverManager.getConnection(url)) {
            System.out.println("[CONNECTED]");
            if (sql == null) return;
            System.out.println("[SQL] " + sql);
            try (Statement s = c.createStatement()) {
                boolean hasRS = s.execute(sql);
                if (hasRS) {
                    ResultSet r = s.getResultSet();
                    ResultSetMetaData m = r.getMetaData();
                    int n = m.getColumnCount();
                    StringBuilder h = new StringBuilder("[HDR]");
                    for (int i=1;i<=n;i++) h.append("\t").append(m.getColumnName(i)).append(":").append(m.getColumnTypeName(i));
                    System.out.println(h);
                    int rows=0;
                    while(r.next()&&rows<50){
                        StringBuilder row=new StringBuilder("[ROW]");
                        for(int i=1;i<=n;i++) row.append("\t").append(r.getString(i)==null?"__NULL__":r.getString(i));
                        System.out.println(row); rows++;
                    }
                    System.out.println("[ROWCOUNT] "+rows);
                } else {
                    System.out.println("[AFFECTED] "+s.getUpdateCount());
                }
                System.out.println("[OK]");
            } catch(SQLException e){ System.out.println("[SQL_ERR] "+e.getMessage()); }
        } catch(Exception e){ System.out.println("[CONN_ERR] "+e.getMessage()); }
    }
}
"@
[System.IO.File]::WriteAllText("$sp\SPProbe.java", $src, [System.Text.Encoding]::ASCII)
javac -cp $jar "$sp\SPProbe.java" -d $sp
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: Compilation failed"; return }
Write-Host "Harness compiled."


$probe_n = 0
function Run-SP([string]$tag, [string]$sql) {
    $script:probe_n++
    $logPath = "$sp\${tag}_$($script:probe_n).log"
    $url2    = $connStr + "Verbosity=5;LogFile=$logPath;"
    $out     = java -cp "$sp;$jar" SPProbe $DC $url2 $sql 2>&1
    Start-Sleep -Milliseconds 300
    $L = if(Test-Path $logPath){ Get-Content $logPath -EA SilentlyContinue }else{ @() }
    $log = [PSCustomObject]@{
        HTTP      = @($L | Where-Object { $_ -match "\[HTTP\|Req\]|(GET|POST|PUT|PATCH|DELETE)\s+https?://" }).Count
        Method    = ([regex]::Match(($L | Where-Object { $_ -match "(GET|POST|PUT|PATCH|DELETE)\s+https?://" } | Select-Object -First 1),"(GET|POST|PUT|PATCH|DELETE)")).Value
        URL       = ([regex]::Match(($L | Where-Object { $_ -match "https?://" } | Select-Object -First 1),"https?://[^\s`"']+")).Value
        Body      = @($L | Where-Object { $_ -match "Request Body|body:|payload|POST body" } | Select-Object -First 5)
        Status4xx = @($L | Where-Object { $_ -match "\[HTTP\|Res\].*\s4\d\d\b" })
        Status5xx = @($L | Where-Object { $_ -match "\[HTTP\|Res\].*\s5\d\d\b" })
        Raw       = $L
    }
    return @{ Tag=$tag; SQL=$sql; Out=($out -join "`n"); Log=$log; LogPath=$logPath }
}

$results = [System.Collections.Generic.List[PSObject]]::new()
function Add-Result([string]$id,[string]$desc,[string]$verdict,[string]$note,[string]$bug="") {
    $r = [PSCustomObject]@{ ID=$id; Description=$desc; Verdict=$verdict; Note=$note; Bug=$bug }
    $results.Add($r)
    $flag = switch($verdict){"PASS"{"✓"}"FAIL"{"✗"}"BUG"{"⚠"}"SKIPPED"{"⊘"}default{"ℹ"}}
    Write-Host "$flag $id | $verdict | $note"
}

# Returns the first element of $cols that case-insensitively matches any of $candidates.
# Falls back to the first candidate if none match (so queries fail visibly rather than silently).
function Find-Col([string[]]$cols, [string[]]$candidates) {
    foreach ($c in $candidates) {
        $m = $cols | Where-Object { $_ -ieq $c } | Select-Object -First 1
        if ($m) { return $m }
    }
    return $candidates[0]
}

function Parse-SysCols([string]$hdrLine) {
    if (-not $hdrLine) { return @() }
    ($hdrLine -replace "^\[HDR\]\t","" -split "\t") | ForEach-Object { ($_ -split ":")[0] }
}
```

---

## Phase 2 — Dual SP discovery (RSB + metadata, cross-matched)

### Step 2a — List SPs via sys_procedures (metadata)

```powershell
Write-Host "`n=== SP DISCOVERY: sys_procedures ==="

# Discover actual column names — never assume they match the canonical names
$rDiscSP = Run-SP "discover_sp" "SELECT * FROM sys_procedures LIMIT 1"
$spCols  = Parse-SysCols ($rDiscSP.Out -split "`n" | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1)
$sp_Name = Find-Col $spCols @("ProcedureName","SpName","StoredProcedureName","Name")
$sp_Desc = Find-Col $spCols @("Description","Desc","Remarks","Comment")
Write-Host "sys_procedures — name col: '$sp_Name'  desc col: '$sp_Desc'"

$rProcs = Run-SP "sys_procs" "SELECT $sp_Name, $sp_Desc FROM sys_procedures ORDER BY $sp_Name"
$metaProcs = @{}
if ($rProcs.Out -match "\[HDR\]") {
    ($rProcs.Out -split "`n") | Where-Object { $_ -match "^\[ROW\]" } | ForEach-Object {
        $vals = ($_ -replace "^\[ROW\]\t","") -split "\t"
        $metaProcs[$vals[0]] = $vals[1]
    }
}
Write-Host "Metadata SPs found: $($metaProcs.Count)"
```

### Step 2b — List SPs via RSB folder (file system)

```powershell
Write-Host "`n=== SP DISCOVERY: RSB files ==="
$rsbFiles  = Get-ChildItem $rsbFolder -Filter "*.rsb" -Recurse | Sort-Object Name
$rsbProcs  = @{}
foreach ($f in $rsbFiles) {
    [xml]$rsb = Get-Content $f.FullName -Raw
    $title    = $rsb.'rsb:script'.'rsb:info'.title
    if (-not $title) { $title = $f.BaseName }
    $rsbProcs[$title] = $f.FullName
}
Write-Host "RSB SPs found: $($rsbProcs.Count)"
```

### Step 2c — Cross-match: RSB vs metadata

```powershell
Write-Host "`n=== CROSS-MATCH: RSB vs sys_procedures ==="
$onlyInRSB  = $rsbProcs.Keys  | Where-Object { -not $metaProcs.ContainsKey($_) }
$onlyInMeta = $metaProcs.Keys | Where-Object { -not $rsbProcs.ContainsKey($_) }
$inBoth     = $rsbProcs.Keys  | Where-Object { $metaProcs.ContainsKey($_) }

Add-Result "DISC-01" "SP count: RSB=$($rsbProcs.Count) matches metadata=$($metaProcs.Count)" `
    $(if($rsbProcs.Count -eq $metaProcs.Count){"PASS"}else{"INFO"}) `
    "OnlyInRSB=$($onlyInRSB.Count) OnlyInMeta=$($onlyInMeta.Count) InBoth=$($inBoth.Count)"
if ($onlyInRSB)  { $onlyInRSB  | ForEach-Object { Add-Result "DISC-RSB-$_"  "SP '$_' in RSB but NOT in sys_procedures"  "BUG" "Driver not exposing RSB-defined SP through metadata" } }
if ($onlyInMeta) { $onlyInMeta | ForEach-Object { Add-Result "DISC-META-$_" "SP '$_' in metadata but NO RSB file found" "BUG" "Metadata exposes SP with no RSB schema file" } }
Write-Host "In both: $($inBoth -join ', ')"
```

### Step 2d — Per-SP: fetch params via sys_procedureparameters

```powershell
$spParams = @{}   # key = SPName, value = list of param objects

# Discover actual column names of sys_procedureparameters once before the loop
$rDiscSPP  = Run-SP "discover_spp" "SELECT * FROM sys_procedureparameters LIMIT 1"
$sppCols   = Parse-SysCols ($rDiscSPP.Out -split "`n" | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1)
$spp_Proc  = Find-Col $sppCols @("ProcedureName","SpName","StoredProcedureName","Name")
$spp_Param = Find-Col $sppCols @("ColumnName","ParameterName","ParamName","Name")
$spp_Dir   = Find-Col $sppCols @("Direction","ParameterDirection","ParamType")
$spp_Req   = Find-Col $sppCols @("IsRequired","Required","IsNullable","Nullable")
$spp_DT    = Find-Col $sppCols @("DataTypeName","DataType","Type","SqlType")
$spp_Desc  = Find-Col $sppCols @("Description","Desc","Remarks","Comment")
Write-Host "sys_procedureparameters — ProcName='$spp_Proc' ParamName='$spp_Param' Direction='$spp_Dir' IsRequired='$spp_Req' DataType='$spp_DT'"

foreach ($spName in $inBoth) {
    $rParams = Run-SP "params_$spName" `
        "SELECT $spp_Proc,$spp_Param,$spp_Dir,$spp_Req,$spp_DT,$spp_Desc FROM sys_procedureparameters WHERE $spp_Proc='$spName' ORDER BY Ordinal"

    $params = @()
    ($rParams.Out -split "`n") | Where-Object { $_ -match "^\[ROW\]" } | ForEach-Object {
        $v = ($_ -replace "^\[ROW\]\t","") -split "\t"
        $params += [PSCustomObject]@{
            SP          = $v[0]
            Name        = $v[1]
            Direction   = $v[2]   # 1=Input 2=Output
            IsRequired  = $v[3]   # true/false
            DataType    = $v[4]
            Description = $v[5]
        }
    }
    $spParams[$spName] = $params
    Write-Host "  $spName — $($params.Count) params (required=$(@($params | Where-Object { $_.IsRequired -eq 'true' -and $_.Direction -eq '1' }).Count) optional=$(@($params | Where-Object { $_.IsRequired -ne 'true' -and $_.Direction -eq '1' }).Count) output=$(@($params | Where-Object { $_.Direction -eq '2' }).Count))"
}
```

### Step 2e — Per-SP: parse RSB file and cross-match params

```powershell
foreach ($spName in $inBoth) {
    $rsbPath = $rsbProcs[$spName]
    [xml]$rsb = Get-Content $rsbPath -Raw
    $info     = $rsb.'rsb:script'.'rsb:info'
    $attrs    = $info.attr

    $rsbEndpoint = $info.url
    $rsbParams   = @{}
    foreach ($a in $attrs) {
        $rsbParams[$a.name] = [PSCustomObject]@{
            Name      = $a.name
            Type      = $a.'xs:type'
            Required  = $a.'xs:required' -eq 'true'
            Direction = if($a.'xs:direction' -eq 'output'){'output'}else{'input'}
            ApiSet    = $a.'api:set'
            Desc      = $a.desc
        }
    }

    $metaInputs = @($spParams[$spName] | Where-Object { $_.Direction -eq '1' })

    # Cross-match: every metadata param should be in RSB
    foreach ($mp in $metaInputs) {
        $rsbP = $rsbParams[$mp.Name]
        if (-not $rsbP) {
            Add-Result "PARAM-MISSING-RSB-$spName-$($mp.Name)" `
                "Param '$($mp.Name)' in metadata but NOT in RSB" "BUG" `
                "SP=$spName Param=$($mp.Name) Type=$($mp.DataType) Required=$($mp.IsRequired)"
        } else {
            # Required flag match
            $metaReq = $mp.IsRequired -eq 'true'
            $rsbReq  = $rsbP.Required
            if ($metaReq -ne $rsbReq) {
                Add-Result "PARAM-REQ-MISMATCH-$spName-$($mp.Name)" `
                    "Required flag mismatch for '$($mp.Name)'" "BUG" `
                    "Metadata=$metaReq RSB=$rsbReq" `
                    "$(if($metaReq -and -not $rsbReq){'UNDERCLAIMED_REQUIRED'}else{'OVERCLAIMED_REQUIRED'})"
            }
            # Type match (loose)
            $metaType = $mp.DataType.ToLower()
            $rsbType  = $rsbP.Type.ToLower()
            $typeOK = ($metaType -match "string|varchar" -and $rsbType -match "string") -or
                      ($metaType -match "int|long"       -and $rsbType -match "int|long") -or
                      ($metaType -match "bool"           -and $rsbType -match "bool") -or
                      ($metaType -match "date|time"      -and $rsbType -match "date|time") -or
                      ($metaType -eq $rsbType)
            if (-not $typeOK) {
                Add-Result "PARAM-TYPE-MISMATCH-$spName-$($mp.Name)" `
                    "DataType mismatch for '$($mp.Name)'" "INFO" `
                    "Metadata=$($mp.DataType) RSB=$($rsbP.Type)"
            }
        }
    }
    # RSB params missing from metadata
    foreach ($rp in $rsbParams.Values | Where-Object { $_.Direction -eq 'input' }) {
        $metaP = $metaInputs | Where-Object { $_.Name -eq $rp.Name }
        if (-not $metaP) {
            Add-Result "PARAM-MISSING-META-$spName-$($rp.Name)" `
                "Param '$($rp.Name)' in RSB but NOT in sys_procedureparameters" "BUG" `
                "SP=$spName Param=$($rp.Name)"
        }
    }
    Write-Host "  $spName endpoint: $rsbEndpoint"
}
```

---

## Phase 3 — API docs validation

```powershell
foreach ($spName in $TARGET_SPS) {
    $rsbEndpoint = ([xml](Get-Content $rsbProcs[$spName] -Raw)).'rsb:script'.'rsb:info'.url
    Write-Host "`n=== FETCHING API DOCS: $spName ($rsbEndpoint) ==="

    # Web search for the specific endpoint
    # web_search("<vendor> API <endpoint_path> reference")
    # web_fetch(<docs_url>)

    # Build ground-truth table from docs and compare (same as before)
    # Mark: MATCH / UNDERCLAIMED_REQUIRED / OVERCLAIMED_REQUIRED / WRONG_FIELD_NAME / WRONG_ENDPOINT / WRONG_METHOD
}
```

---

## Phase 4 — Test data

*(Same as before — query sys_tables/sys_tablecolumns for existing data; INSERT or curl if needed.)*

---

## Phase 5 — Required param client-side enforcement

**Core rule:** If a param is marked `IsRequired=true` in sys_procedureparameters AND `xs:required="true"` in the RSB, then the driver MUST reject the call at the driver level — **before any HTTP request fires** — for ALL of these:
- Param missing entirely from the EXECUTE call
- Param passed as empty string `''`
- Param passed as `NULL`
- Param passed as whitespace-only `'   '`

**HTTP=0 in the log = PASS. Any HTTP request for these cases = BUG.**

```powershell
foreach ($spName in $TARGET_SPS) {
    $requiredParams = @($spParams[$spName] | Where-Object { $_.IsRequired -eq 'true' -and $_.Direction -eq '1' })
    $optionalParams = @($spParams[$spName] | Where-Object { $_.IsRequired -ne 'true' -and $_.Direction -eq '1' })

    Write-Host "`n=== REQUIRED PARAM ENFORCEMENT: $spName (${$requiredParams.Count} required) ==="

    # Build full valid EXECUTE with all required params having valid values
    $validExec = Build-ValidExec $spName $requiredParams

    foreach ($rp in $requiredParams) {
        $pn = $rp.Name

        # NR-a: Missing entirely
        $sql = Remove-Param $validExec $pn
        $r   = Run-SP "NR_${spName}_${pn}_a_missing" $sql
        $noHTTP = $r.Log.HTTP -eq 0
        $hasErr = $r.Out -match "\[SQL_ERR\]"
        Add-Result "NR-$spName-$pn-a" "Required '$pn' missing — driver must error before HTTP" `
            $(if($noHTTP -and $hasErr){"PASS"}else{"FAIL"}) `
            "HTTP=$($r.Log.HTTP) Error=$(if($hasErr){'YES'}else{'NO'})" `
            $(if($r.Log.HTTP -gt 0){"BUG: MISSING_REQUIRED_SENT_TO_SERVER — HTTP fired without required param '$pn'"})

        # NR-b: Empty string
        $sql = Set-Param $validExec $pn "''"
        $r   = Run-SP "NR_${spName}_${pn}_b_empty" $sql
        $noHTTP = $r.Log.HTTP -eq 0
        $hasErr = $r.Out -match "\[SQL_ERR\]"
        Add-Result "NR-$spName-$pn-b" "Required '$pn' = '' (empty) — driver must error before HTTP" `
            $(if($noHTTP -and $hasErr){"PASS"}else{"FAIL"}) `
            "HTTP=$($r.Log.HTTP) Error=$(if($hasErr){'YES'}else{'NO'})" `
            $(if($r.Log.HTTP -gt 0){"BUG: EMPTY_REQUIRED_SENT_TO_SERVER — empty string for '$pn' reached server"})

        # NR-c: NULL
        $sql = Set-Param $validExec $pn "NULL"
        $r   = Run-SP "NR_${spName}_${pn}_c_null" $sql
        $noHTTP = $r.Log.HTTP -eq 0
        Add-Result "NR-$spName-$pn-c" "Required '$pn' = NULL — driver must error before HTTP" `
            $(if($noHTTP){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
            $(if($r.Log.HTTP -gt 0){"BUG: NULL_REQUIRED_SENT_TO_SERVER"})

        # NR-d: Whitespace only
        $sql = Set-Param $validExec $pn "'   '"
        $r   = Run-SP "NR_${spName}_${pn}_d_whitespace" $sql
        $noHTTP = $r.Log.HTTP -eq 0
        Add-Result "NR-$spName-$pn-d" "Required '$pn' = '   ' (whitespace) — driver must error before HTTP" `
            $(if($noHTTP){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)" `
            $(if($r.Log.HTTP -gt 0){"BUG: WHITESPACE_REQUIRED_SENT_TO_SERVER"})
    }
}
```

---

## Phase 6 — Comprehensive negative test cases by data type

For each param (required AND optional), generate all applicable negative tests based on
its declared data type. The goal: the driver or API must return a clear error for every
invalid input — never silently accept it.

```powershell
function Get-NegativeTestCases([PSCustomObject]$param) {
    $pn   = $param.Name
    $dt   = $param.DataType.ToLower()
    $desc = $param.Description
    $cases = @()

    # ── STRING / VARCHAR params ────────────────────────────────────────────
    if ($dt -match "string|varchar|text|char") {
        # Wrong data type: integer
        $cases += @{ id="NT-$pn-int";    val="12345";          desc="String param '$pn' — pass integer value";    expect="Error or type coercion mismatch" }
        # Wrong data type: boolean
        $cases += @{ id="NT-$pn-bool";   val="true";           desc="String param '$pn' — pass boolean literal";  expect="API error if boolean not valid for field" }
        # Wrong data type: float
        $cases += @{ id="NT-$pn-float";  val="3.14";           desc="String param '$pn' — pass decimal value";    expect="API error or implicit coercion" }
        # SQL injection attempt (driver should pass through; API handles)
        $cases += @{ id="NT-$pn-sqlinj"; val="' OR '1'='1";   desc="String param '$pn' — SQL injection pattern"; expect="API treats as literal string; no DB error" }
        # Extremely long string
        $cases += @{ id="NT-$pn-long";   val="'$('X'*5001)'"; desc="String param '$pn' — 5001-char string";      expect="API validation error (too long) or driver truncation notice" }
        # Special characters
        $cases += @{ id="NT-$pn-special";val="'<>\\`"/{}|'";  desc="String param '$pn' — special chars";         expect="API accepts or returns 400; driver surfaces error" }

        # File/URL/enum detection from description
        if ($desc -match "url|endpoint|uri") {
            $cases += @{ id="NT-$pn-badurl";    val="'not_a_url'";            desc="URL param '$pn' — invalid URL format";         expect="API returns INVALID_VALUE" }
            $cases += @{ id="NT-$pn-urlscheme"; val="'ftp://example.com'";   desc="URL param '$pn' — wrong scheme (ftp:// not http)"; expect="API or driver error" }
            $cases += @{ id="NT-$pn-urlspace";  val="'http://exa mple.com'"; desc="URL param '$pn' — space in URL";                 expect="API error" }
        }

        if ($desc -match "email") {
            $cases += @{ id="NT-$pn-bademail1"; val="'notanemail'";       desc="Email param '$pn' — missing @ symbol";  expect="API validation error" }
            $cases += @{ id="NT-$pn-bademail2"; val="'@nodomain'";        desc="Email param '$pn' — missing local part"; expect="API validation error" }
            $cases += @{ id="NT-$pn-bademail3"; val="'user@'";            desc="Email param '$pn' — missing domain";     expect="API validation error" }
        }

        if ($desc -match "phone|mobile|number") {
            $cases += @{ id="NT-$pn-badphone1"; val="'abc-def-ghij'";   desc="Phone param '$pn' — letters instead of digits"; expect="API validation error" }
            $cases += @{ id="NT-$pn-badphone2"; val="'12'";              desc="Phone param '$pn' — too short";                 expect="API validation error" }
        }

        if ($desc -match "status|state|type|enum|kind" -or $desc -match "\(.*\|.*\)") {
            # Extract known enum values from description if possible, then send a bad one
            $cases += @{ id="NT-$pn-badenum";   val="'INVALID_ENUM_XYZ'"; desc="Enum param '$pn' — value not in allowed set"; expect="API INVALID_VALUE or INVALID_ENUM" }
            $cases += @{ id="NT-$pn-enumcase";  val="'$(($validValue).ToLower())'"; desc="Enum param '$pn' — wrong case (lowercase)"; expect="API INVALID_VALUE if case-sensitive" }
        }
    }

    # ── INTEGER / LONG params ─────────────────────────────────────────────
    if ($dt -match "int|long|number(?!.*string)") {
        $cases += @{ id="NT-$pn-str";   val="'hello'";   desc="Int param '$pn' — pass string value";  expect="Driver or API type error" }
        $cases += @{ id="NT-$pn-float"; val="'3.14'";    desc="Int param '$pn' — pass decimal value"; expect="Driver rejects or API truncates" }
        $cases += @{ id="NT-$pn-bool";  val="'true'";    desc="Int param '$pn' — pass boolean";       expect="Type error" }
        $cases += @{ id="NT-$pn-neg";   val="-1";        desc="Int param '$pn' — negative value";     expect="API validation error if must be positive" }
        $cases += @{ id="NT-$pn-zero";  val="0";         desc="Int param '$pn' — zero value";         expect="API validation error if min > 0" }
        $cases += @{ id="NT-$pn-huge";  val="9999999999999"; desc="Int param '$pn' — overflow value"; expect="Driver or API rejects" }
    }

    # ── FLOAT / DECIMAL params ────────────────────────────────────────────
    if ($dt -match "float|decimal|double|real|numeric") {
        $cases += @{ id="NT-$pn-str";     val="'hello'"; desc="Decimal param '$pn' — pass string";   expect="Type error" }
        $cases += @{ id="NT-$pn-bool";    val="'true'";  desc="Decimal param '$pn' — pass boolean";  expect="Type error" }
        $cases += @{ id="NT-$pn-neg";     val="-0.01";   desc="Decimal param '$pn' — negative";      expect="API error if must be positive" }
        $cases += @{ id="NT-$pn-toomany"; val="'1.123456789012345678'"; desc="Decimal — excessive precision"; expect="API truncation or error" }
    }

    # ── BOOLEAN params ────────────────────────────────────────────────────
    if ($dt -match "bool") {
        $cases += @{ id="NT-$pn-str";  val="'yes'";  desc="Bool param '$pn' — string 'yes' instead of true/false"; expect="Type error or coercion" }
        $cases += @{ id="NT-$pn-int";  val="2";      desc="Bool param '$pn' — integer 2 (not 0 or 1)";             expect="Type error" }
        $cases += @{ id="NT-$pn-str2"; val="'TRUE'"; desc="Bool param '$pn' — uppercase string 'TRUE'";            expect="May work or may error; document behaviour" }
    }

    # ── DATE / DATETIME / TIMESTAMP params ───────────────────────────────
    if ($dt -match "date|datetime|timestamp") {
        $cases += @{ id="NT-$pn-int";      val="12345";             desc="Date param '$pn' — pass integer";           expect="Type error" }
        $cases += @{ id="NT-$pn-str";      val="'not_a_date'";      desc="Date param '$pn' — invalid date string";    expect="Driver or API rejects" }
        $cases += @{ id="NT-$pn-wrongfmt"; val="'15/06/2025'";      desc="Date param '$pn' — DD/MM/YYYY instead of ISO"; expect="API error (wrong format)" }
        $cases += @{ id="NT-$pn-notime";   val="'2025-06-15'";      desc="Datetime param '$pn' — date-only string";   expect="API accepts or requires time component" }
        $cases += @{ id="NT-$pn-future";   val="'2099-12-31T23:59:59Z'"; desc="Date param '$pn' — far future date";  expect="API accepts or rejects depending on field" }
        $cases += @{ id="NT-$pn-past";     val="'1900-01-01T00:00:00Z'"; desc="Date param '$pn' — far past date";    expect="API accepts or rejects" }
        $cases += @{ id="NT-$pn-bool";     val="'true'";            desc="Date param '$pn' — pass boolean string";    expect="Type error" }
    }

    # ── FILE / BINARY / UPLOAD params ────────────────────────────────────
    if ($desc -match "file|image|photo|attachment|upload|document|pdf|png|jpg|jpeg|gif|svg|csv|zip") {
        # Detect expected extensions from description
        $expectedExts = @()
        if ($desc -match "png")  { $expectedExts += "png" }
        if ($desc -match "jpg|jpeg") { $expectedExts += "jpg" }
        if ($desc -match "gif")  { $expectedExts += "gif" }
        if ($desc -match "pdf")  { $expectedExts += "pdf" }
        if ($desc -match "csv")  { $expectedExts += "csv" }
        if ($desc -match "zip")  { $expectedExts += "zip" }
        if ($desc -match "svg")  { $expectedExts += "svg" }
        if ($expectedExts.Count -eq 0) { $expectedExts = @("png","jpg","pdf","csv","zip") }

        $wrongExts = @("exe","bat","sh","js","php","html","xml","txt","docx","mp4","mp3","iso") |
                     Where-Object { $expectedExts -notcontains $_ }
        foreach ($ext in $wrongExts[0..4]) {
            $cases += @{
                id     = "NT-$pn-ext-$ext"
                val    = "'fake_file.$ext'"
                desc   = "File param '$pn' — wrong extension .$ext (expected: $($expectedExts -join ','))"
                expect = "API or driver rejects: INVALID_FILE_TYPE or UNSUPPORTED_MEDIA_TYPE"
            }
        }

        # Empty filename
        $cases += @{ id="NT-$pn-emptyfile"; val="''";                  desc="File param '$pn' — empty filename";     expect="Driver error before HTTP" }
        # Path traversal
        $cases += @{ id="NT-$pn-traversal"; val="'../../etc/passwd'";  desc="File param '$pn' — path traversal";     expect="Driver or API rejects; no file read attempt" }
        # Non-file string
        $cases += @{ id="NT-$pn-notafile";  val="'this_is_not_a_file'"; desc="File param '$pn' — non-existent path"; expect="Error: file not found" }
        # Oversized (simulate with description)
        $cases += @{ id="NT-$pn-oversize";  val="'$(Get-LargeFilePath)'"; desc="File param '$pn' — oversized file";  expect="API 413 or driver size limit error" }
    }

    # ── ID / REFERENCE params (string IDs that reference entities) ───────
    if ($desc -match "\bid\b|identifier|reference|key" -and $dt -match "string") {
        $cases += @{ id="NT-$pn-fakeid";  val="'FAKE_ID_THAT_DOES_NOT_EXIST_00000'"; desc="ID param '$pn' — non-existent ID"; expect="API 404 NOT_FOUND" }
        $cases += @{ id="NT-$pn-emptyid"; val="''";   desc="ID param '$pn' — empty ID";   expect="Driver error before HTTP (required ID)" }
        $cases += @{ id="NT-$pn-intid";   val="99999"; desc="ID param '$pn' — integer instead of string ID"; expect="Type error or API NOT_FOUND" }
        $cases += @{ id="NT-$pn-sqlinj2"; val="'1; DROP TABLE orders; --'"; desc="ID param '$pn' — injection in ID field"; expect="API treats as literal; returns NOT_FOUND" }
    }

    # ── CURRENCY / AMOUNT params ──────────────────────────────────────────
    if ($desc -match "amount|price|cost|currency|money|value") {
        $cases += @{ id="NT-$pn-str";     val="'fifty'";    desc="Amount param '$pn' — word instead of number";   expect="Type error" }
        $cases += @{ id="NT-$pn-neg";     val="-100";       desc="Amount param '$pn' — negative amount";          expect="API validation error if must be positive" }
        $cases += @{ id="NT-$pn-zero";    val="0";          desc="Amount param '$pn' — zero amount";              expect="API accepts or rejects depending on field" }
        $cases += @{ id="NT-$pn-symbol";  val="'$100'";     desc="Amount param '$pn' — dollar sign in value";     expect="API type error" }
        $cases += @{ id="NT-$pn-toobig";  val="9999999999"; desc="Amount param '$pn' — unreasonably large value"; expect="API validation error" }
    }

    return $cases
}

# Run all negative test cases for each SP
foreach ($spName in $TARGET_SPS) {
    $allParams  = @($spParams[$spName] | Where-Object { $_.Direction -eq '1' })
    $reqParams  = @($allParams | Where-Object { $_.IsRequired -eq 'true' })
    $validExec  = Build-ValidExec $spName $reqParams

    Write-Host "`n=== NEGATIVE TYPE TESTS: $spName ==="

    foreach ($param in $allParams) {
        $testCases = Get-NegativeTestCases $param
        foreach ($tc in $testCases) {
            # Build EXECUTE with this param set to the negative value, all required params valid
            $execSQL = if ($param.IsRequired -eq 'true') {
                Set-Param $validExec $param.Name $tc.val
            } else {
                $validExec + ", $($param.Name)=$($tc.val)"
            }
            $fullSQL = "EXECUTE $spName $execSQL"
            $r = Run-SP $tc.id $fullSQL

            # Determine pass/fail
            $isError = $r.Out -match "\[SQL_ERR\]|\[CONN_ERR\]"
            $noHTTP  = $r.Log.HTTP -eq 0
            # For missing/empty/null/whitespace on required: must be no HTTP
            $verdict = if ($tc.id -match "_missing|_empty|_null|_whitespace") {
                if ($noHTTP -and $isError) { "PASS" } else { "FAIL" }
            } else {
                # For wrong-type / invalid-value: either driver error OR API error is acceptable
                if ($isError) { "PASS" } else { "INFO" }
            }
            $bugNote = if ($verdict -eq "FAIL") {
                if ($r.Log.HTTP -gt 0 -and ($tc.id -match "_missing|_empty|_null|_whitespace")) {
                    "BUG: Driver sent HTTP with invalid/empty required param '$($param.Name)'"
                } elseif (-not $isError) {
                    "WARN: No error raised for invalid input — check if API silently accepted bad value"
                } else { "" }
            } else { "" }

            Add-Result $tc.id $tc.desc $verdict `
                "HTTP=$($r.Log.HTTP) Error=$(if($isError){'YES'}else{'NO'}) Expected=$($tc.expect)" `
                $bugNote
        }
    }
}
```

---

## Phase 7 — Positive permutation tests

```powershell
foreach ($spName in $TARGET_SPS) {
    $reqParams  = @($spParams[$spName] | Where-Object { $_.IsRequired -eq 'true' -and $_.Direction -eq '1' })
    $optParams  = @($spParams[$spName] | Where-Object { $_.IsRequired -ne 'true' -and $_.Direction -eq '1' })
    $outParams  = @($spParams[$spName] | Where-Object { $_.Direction -eq '2' })

    Write-Host "`n=== POSITIVE TESTS: $spName (${$reqParams.Count} req, ${$optParams.Count} opt) ==="

    # P1: Required only (baseline)
    $reqExec = Build-ValidExec $spName $reqParams
    $r = Run-SP "P1_${spName}_req_only" "EXECUTE $spName $reqExec"
    $ok = $r.Out -match "\[OK\]|\[HDR\]|\[ROW\]|\[ROWCOUNT\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
    Add-Result "P1-$spName" "Required params only — happy path" `
        $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP) Method=$($r.Log.Method)"

    # Verify: no optional param keys in request body
    foreach ($op in $optParams) {
        $apiFieldName = Get-ApiFieldName $op.Name $spName
        $inBody = $r.Log.Body | Where-Object { $_ -match [regex]::Escape($apiFieldName) }
        if ($inBody) {
            Add-Result "D9-$spName-$($op.Name)" "Optional '$($op.Name)' absent from body when not supplied" "FAIL" `
                "BUG: Optional param key '$apiFieldName' found in request body for P1 (required-only call)" `
                "EMPTY_OPTIONAL_SENT_TO_SERVER"
        }
    }

    # Verify output columns populated
    foreach ($op in $outParams) {
        $outVal = ($r.Out -split "`n") | Where-Object { $_ -match "^\[ROW\]" } |
                  Select-Object -First 1 | ForEach-Object { ($_ -replace "^\[ROW\]\t","") -split "\t" }
        if (-not $outVal -or $outVal -contains "__NULL__") {
            Add-Result "D4-$spName-$($op.Name)" "Output column '$($op.Name)' non-null on success" "INFO" `
                "Output column was NULL/missing — verify RSB output field path"
        }
    }

    # P2…P(N+1): Required + each optional param individually
    $optN = [Math]::Min($optParams.Count, 4)   # cap at 4 for individual tests
    for ($i = 0; $i -lt $optN; $i++) {
        $op     = $optParams[$i]
        $optVal = Get-ValidValueForParam $op
        $sql    = "EXECUTE $spName $reqExec, $($op.Name)=$optVal"
        $r      = Run-SP "P$(2+$i)_${spName}_plus_$($op.Name)" $sql
        $ok     = $r.Out -match "\[OK\]|\[HDR\]|\[ROWCOUNT\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
        Add-Result "P$(2+$i)-$spName-$($op.Name)" "Required + optional '$($op.Name)'" `
            $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
        # Verify: this optional param IS in body
        $apiField = Get-ApiFieldName $op.Name $spName
        $inBody   = $r.Log.Body | Where-Object { $_ -match [regex]::Escape($apiField) }
        Add-Result "D10-$spName-$($op.Name)" "Optional '$($op.Name)' present in body when supplied" `
            $(if($inBody){"PASS"}else{"FAIL"}) "BodyLine=$($inBody | Select-Object -First 1)"
    }

    # P_last: All required + all optional
    if ($optParams.Count -gt 1) {
        $allOptVals = ($optParams | ForEach-Object { "$($_.Name)=$(Get-ValidValueForParam $_)" }) -join ", "
        $sql = "EXECUTE $spName $reqExec, $allOptVals"
        $r   = Run-SP "PALL_${spName}_all_params" $sql
        $ok  = $r.Out -match "\[OK\]|\[HDR\]|\[ROWCOUNT\]|\[AFFECTED\]" -and $r.Out -notmatch "\[SQL_ERR\]"
        Add-Result "PALL-$spName" "All params (required + all optional)" `
            $(if($ok){"PASS"}else{"FAIL"}) "HTTP=$($r.Log.HTTP)"
    }

    # Optional param empty/null/whitespace — should be omitted from body
    foreach ($op in $optParams) {
        $apiField = Get-ApiFieldName $op.Name $spName
        foreach ($emptyVal in @("''","NULL","'   '")) {
            $tag     = "NO_${spName}_$($op.Name)_$(if($emptyVal -eq "''"){'empty'}elseif($emptyVal -eq 'NULL'){'null'}else{'ws'})"
            $sql     = "EXECUTE $spName $reqExec, $($op.Name)=$emptyVal"
            $r       = Run-SP $tag $sql
            $inBody  = $r.Log.Body | Where-Object { $_ -match [regex]::Escape($apiField) }
            Add-Result "NO-$spName-$($op.Name)-$(if($emptyVal -eq "''"){'empty'}elseif($emptyVal -eq 'NULL'){'null'}else{'ws'})" `
                "Optional '$($op.Name)' $emptyVal — key must NOT appear in request body" `
                $(if(-not $inBody){"PASS"}else{"FAIL"}) "BodyHit=$(if($inBody){'YES — BUG'}else{'NO — correct'})" `
                $(if($inBody){"BUG: EMPTY_OPTIONAL_SENT_TO_SERVER — '$apiField' key in body with empty/null/ws value"})
        }
    }

    # Entity-state negative tests
    Add-Result "N-State-$spName" "Wrong entity state (already cancelled/completed)" "SKIPPED" `
        "Manual: run SP on entity in wrong state, verify API returns business logic error"
    Add-Result "N-Repeat-$spName" "Repeat execution — idempotency check" "SKIPPED" `
        "Manual: run same SP twice on same entity, verify 2nd run returns state conflict"
}
```

---

## Phase 8 — Driver behaviour checks

```powershell
Write-Host "`n=== DRIVER BEHAVIOUR CHECKS ==="
# D1: 4xx surfaces as SQL error
$r4xx = $results | Where-Object { $_.Note -match "404|400|422|409" }
Add-Result "D1-4xx-surfaced" "All 4xx API errors surface as SQLException (none swallowed)" `
    $(if(($r4xx | Where-Object { $_.Verdict -eq "FAIL" -and $_.Bug -match "swallowed" }).Count -eq 0){"PASS"}else{"FAIL"}) `
    "4xx results checked=$($r4xx.Count)"

# D5: Empty body for no-body SPs
# D6-D8: Required empty/null/whitespace blocked at driver — already covered in Phase 5
# D9-D11: Optional body presence — already covered in Phase 7
```

---

## Phase 9 — Report + token tracking

```powershell
Write-Host "`n═══════════════════════════════════════════════════════════════════"
Write-Host "Stored Procedure Validation Report"
Write-Host "═══════════════════════════════════════════════════════════════════"
Write-Host "Driver    : $($DC -replace 'cdata\.jdbc\.','')"
Write-Host "SPs tested: $($TARGET_SPS -join ', ')"
Write-Host ""
$results | Format-Table -AutoSize ID, Description, Verdict, Note
$p = ($results | Where-Object Verdict -eq "PASS").Count
$f = ($results | Where-Object Verdict -eq "FAIL").Count
$b = ($results | Where-Object Bug -ne "").Count
Write-Host "Summary: PASS=$p FAIL=$f BUG=$b SKIPPED=$(($results | Where-Object Verdict -eq 'SKIPPED').Count)"

# Bugs
$bugs = $results | Where-Object { $_.Bug -ne "" -or $_.Verdict -eq "FAIL" }
if($bugs.Count -gt 0){
    Write-Host "`n─── BUGS / FAILURES ──────────────────────────────────────────────────"
    $bugs | ForEach-Object { Write-Host "  $($_.ID) | $($_.Bug -or $_.Note)" }
}

# Token tracking (see token-tracker.md)
# Get actual token counts from the API and record
$USAGE_LOG = "$env:USERPROFILE\.cdata-qa\usage-log.json"
$PRICING   = @{
    "claude-sonnet-4-6" = @{ Input=3.00; Output=15.00; CacheWrite=3.75; CacheRead=0.30 }
    "default"           = @{ Input=3.00; Output=15.00; CacheWrite=3.75; CacheRead=0.30 }
}

function Get-ActualTokens {
    param([string]$ConversationSummary, [string]$Model = "claude-sonnet-4-6")
    $body = @{
        model="$Model"; max_tokens=5
        messages=@(@{ role="user"; content="QA session summary for token tracking: $ConversationSummary. Reply OK." })
    } | ConvertTo-Json -Depth 5 -Compress
    try {
        $r = Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" -Method POST `
            -Headers @{ "Content-Type"="application/json"; "anthropic-version"="2023-06-01" } `
            -Body $body -ErrorAction Stop
        return @{ InputTokens=$r.usage.input_tokens; OutputTokens=$r.usage.output_tokens
                  CacheWriteTokens=([int]$r.usage.cache_creation_input_tokens)
                  CacheReadTokens=([int]$r.usage.cache_read_input_tokens); Success=$true }
    } catch { return @{ Success=$false; Error=$_.Exception.Message } }
}

function Record-Usage {
    param([string]$Command,[string]$Driver,[string]$Table,[string]$Model="claude-sonnet-4-6",[string]$SessionSummary="")
    if (-not $SessionSummary){ $SessionSummary="Command=$Command Driver=$Driver Table=$Table Probes=$script:probe_n Results=$($script:results.Count)" }
    Write-Host "  Fetching actual token counts from API..."
    $usage = Get-ActualTokens -ConversationSummary $SessionSummary -Model $Model
    if (-not $usage.Success) { Write-Host "  WARNING: Token tracking unavailable — $($usage.Error)"; return }
    $dir = Split-Path $USAGE_LOG
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory $dir -Force | Out-Null }
    $r=$PRICING[$Model] ?? $PRICING["default"]
    $tc=(($usage.InputTokens/1e6)*$r.Input)+(($usage.OutputTokens/1e6)*$r.Output)+(($usage.CacheWriteTokens/1e6)*$r.CacheWrite)+(($usage.CacheReadTokens/1e6)*$r.CacheRead)
    $rec=[PSCustomObject]@{ Timestamp=(Get-Date -F "yyyy-MM-dd HH:mm:ss"); Command=$Command; Driver=$Driver; Table=$Table; Model=$Model
        InputTokens=$usage.InputTokens; OutputTokens=$usage.OutputTokens; CacheWriteTokens=$usage.CacheWriteTokens; CacheReadTokens=$usage.CacheReadTokens
        TotalTokens=($usage.InputTokens+$usage.OutputTokens+$usage.CacheWriteTokens+$usage.CacheReadTokens)
        TotalCostUSD=[math]::Round($tc,6); Notes="actual" }
    $ex=if(Test-Path $USAGE_LOG){try{Get-Content $USAGE_LOG -Raw|ConvertFrom-Json}catch{@()}}else{@()}
    @($ex)+$rec | ConvertTo-Json -Depth 5 | Set-Content $USAGE_LOG -Encoding UTF8
    Write-Host ("  TOKEN (actual): $Command | In={0} Out={1} Total={2} Cost=`${3}" -f $usage.InputTokens,$usage.OutputTokens,$rec.TotalTokens,[math]::Round($tc,4))
}

Record-Usage `
    -Command        "/sp" `
    -Driver         "$($DC -replace 'cdata\.jdbc\.','') JDBC" `
    -Table          "$($TARGET_SPS -join ',')" `
    -Model          "claude-sonnet-4-6"
```

---

## Appendix A — Helper functions

```powershell
function Build-ValidExec([string]$spName, [PSCustomObject[]]$reqParams) {
    $parts = $reqParams | ForEach-Object { "$($_.Name)='$(Get-ValidValueForParam $_)'" }
    return $parts -join ", "
}

function Get-ValidValueForParam([PSCustomObject]$param) {
    $dt = $param.DataType.ToLower()
    switch -Regex ($dt) {
        "int|long|number"       { return "1" }
        "float|decimal|double"  { return "1.0" }
        "bool"                  { return "true" }
        "date(?!time)"          { return "2025-06-15" }
        "datetime|timestamp"    { return "2025-06-15T10:00:00Z" }
        default                 { return "TestValue_$(Get-Random -Max 9999)" }
    }
}

function Set-Param([string]$exec, [string]$paramName, [string]$newVal) {
    if ($exec -match "$paramName='[^']*'") { return $exec -replace "$paramName='[^']*'","$paramName=$newVal" }
    if ($exec -match "$paramName=[^\s,]+") { return $exec -replace "$paramName=[^\s,]+","$paramName=$newVal" }
    return "$exec, $paramName=$newVal"
}

function Remove-Param([string]$exec, [string]$paramName) {
    return ($exec -replace ",?\s*$paramName=[^,]+","").Trim().TrimStart(",").Trim()
}

function Get-ApiFieldName([string]$paramName, [string]$spName) {
    [xml]$rsb = Get-Content $rsbProcs[$spName] -Raw
    $attr = $rsb.'rsb:script'.'rsb:info'.attr | Where-Object { $_.name -eq $paramName }
    if ($attr -and $attr.'api:set') { return $attr.'api:set' }
    return $paramName -replace '([A-Z])', '_$1' -replace '^_','' -ToLower()   # PascalCase → snake_case fallback
}

function Get-LargeFilePath { return "$env:TEMP\large_test_$(Get-Random).bin" }
```

---

## Appendix B — Bug classification

| Bug Type | Description |
|---|---|
| `MISSING_REQUIRED_SENT_TO_SERVER` | Driver sent HTTP with a required param entirely absent from the EXECUTE call |
| `EMPTY_REQUIRED_SENT_TO_SERVER` | Required param passed as `''` — HTTP fired instead of driver error |
| `NULL_REQUIRED_SENT_TO_SERVER` | Required param passed as `NULL` — HTTP fired instead of driver error |
| `WHITESPACE_REQUIRED_SENT_TO_SERVER` | Required param passed as `'   '` — HTTP fired instead of driver error |
| `EMPTY_OPTIONAL_SENT_TO_SERVER` | Optional param passed as `''`/`NULL`/whitespace — key appears in request body |
| `WRONG_ENDPOINT` | SP hits wrong URL |
| `WRONG_METHOD` | SP uses wrong HTTP method |
| `WRONG_PARAM_NAME` | Param mapped to wrong API field name |
| `OVERCLAIMED_REQUIRED` | Driver requires param the API says is optional |
| `UNDERCLAIMED_REQUIRED` | Driver marks required param as optional |
| `MISSING_PARAM` | API param not exposed in driver at all |
| `ERROR_SWALLOWED` | Driver returns success on API 4xx |
| `OUTPUT_MISSING` | API response field not surfaced as output column |
| `RSB_META_MISMATCH` | RSB file and sys_procedureparameters disagree on param existence, required flag, or type |
| `WRONG_TYPE_ACCEPTED` | Driver accepted a value of the wrong data type without error |
| `INVALID_FILE_EXT_ACCEPTED` | Driver accepted a file with a wrong extension without error |

---

## Appendix C — Quick reference: system tables

> **Always discover column names first — never hardcode them.**
> Column names vary across drivers (e.g. `ProcedureName` vs `SpName`, `ColumnName` vs `ParameterName`).
> Use `SELECT * FROM sys_<table> LIMIT 1` to read the HDR line, then use `Find-Col` to map to the
> actual column name before running any filtered query.

```powershell
# Step 1 — discover
$rDisc  = Run-SP "discover_sp" "SELECT * FROM sys_procedures LIMIT 1"
$spCols = Parse-SysCols ($rDisc.Out -split "`n" | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1)
$sp_Name = Find-Col $spCols @("ProcedureName","SpName","Name")
$sp_Desc = Find-Col $spCols @("Description","Desc","Remarks")

# Step 2 — query with actual column names
Run-SP "list_sp" "SELECT $sp_Name, $sp_Desc FROM sys_procedures ORDER BY $sp_Name"
```

```powershell
# Discover sys_procedureparameters
$rDiscSPP = Run-SP "discover_spp" "SELECT * FROM sys_procedureparameters LIMIT 1"
$sppCols  = Parse-SysCols ($rDiscSPP.Out -split "`n" | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1)
$spp_Proc  = Find-Col $sppCols @("ProcedureName","SpName","Name")
$spp_Param = Find-Col $sppCols @("ColumnName","ParameterName","ParamName","Name")
$spp_Dir   = Find-Col $sppCols @("Direction","ParameterDirection","ParamType")
$spp_Req   = Find-Col $sppCols @("IsRequired","Required","IsNullable")
$spp_DT    = Find-Col $sppCols @("DataTypeName","DataType","Type")
$spp_Desc  = Find-Col $sppCols @("Description","Desc","Remarks")

Run-SP "sp_params" "SELECT $spp_Proc,$spp_Param,$spp_Dir,$spp_Req,$spp_DT,$spp_Desc FROM sys_procedureparameters WHERE $spp_Proc='YourSPName' ORDER BY Ordinal"
-- Direction value: 1=Input  2=Output
```

```powershell
# Discover sys_tablecolumns
$rDiscTC = Run-SP "discover_tc" "SELECT * FROM sys_tablecolumns LIMIT 1"
$tcCols  = Parse-SysCols ($rDiscTC.Out -split "`n" | Where-Object { $_ -match "^\[HDR\]" } | Select-Object -First 1)
$tc_Table = Find-Col $tcCols @("TableName","Table","TableId")
$tc_Col   = Find-Col $tcCols @("ColumnName","Name","Column")
$tc_DT    = Find-Col $tcCols @("DataTypeName","DataType","Type")
$tc_Null  = Find-Col $tcCols @("IsNullable","Nullable","AllowNull")

Run-SP "tc_cols" "SELECT $tc_Col,$tc_DT,$tc_Null FROM sys_tablecolumns WHERE $tc_Table='YourTable'"
```
