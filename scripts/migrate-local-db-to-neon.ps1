$ErrorActionPreference = "Stop"

function Get-PostgresConnectionParts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConnectionString,
        [Parameter(Mandatory = $true)]
        [string]$ParameterName
    )

    $normalizedConnectionString = $ConnectionString.Trim()
    if ($normalizedConnectionString -match "^(?:[$>]\s*)?(?i:psql)\s+(?<argument>.+?)\s*;?$") {
        $normalizedConnectionString = $matches["argument"].Trim()
        if ($normalizedConnectionString -match "^(['""])(?<url>.+)\1$") {
            $normalizedConnectionString = $matches["url"]
        }
    }
    else {
        $normalizedConnectionString = $normalizedConnectionString.TrimEnd(";").Trim().Trim([char[]]@("'", '"'))
    }

    $urlMatch = [regex]::Match(
        $normalizedConnectionString,
        "^(?<scheme>postgres(?:ql)?(?:\+asyncpg|\+psycopg)?)://(?<userinfo>.+)@(?<host>[^:/?#]+)(?::(?<port>\d+))?/(?<database>[^/?#]+)(?:\?(?<query>[^#]*))?$",
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $urlMatch.Success) {
        throw "$ParameterName is not a valid PostgreSQL URL. Paste only the URL (starting with postgresql:// or postgres://), or the complete `psql 'URL'` command from Neon."
    }

    $scheme = $urlMatch.Groups["scheme"].Value.ToLowerInvariant()
    if ($scheme -notin @("postgresql", "postgres", "postgresql+asyncpg", "postgresql+psycopg")) {
        throw "$ParameterName must use a PostgreSQL URL."
    }

    $credentials = $urlMatch.Groups["userinfo"].Value.Split(":", 2)
    if ($credentials.Count -ne 2 -or [string]::IsNullOrWhiteSpace($credentials[0])) {
        throw "$ParameterName must include a database username and password."
    }

    $databaseName = [System.Uri]::UnescapeDataString($urlMatch.Groups["database"].Value)
    if ([string]::IsNullOrWhiteSpace($databaseName)) {
        throw "$ParameterName must include a database name."
    }

    $port = 5432
    if ($urlMatch.Groups["port"].Success -and
        (-not [int]::TryParse($urlMatch.Groups["port"].Value, [ref]$port) -or
            $port -lt 1 -or $port -gt 65535)) {
        throw "$ParameterName contains an invalid PostgreSQL port."
    }

    return [PSCustomObject]@{
        Host     = $urlMatch.Groups["host"].Value
        Port     = $port
        Database = $databaseName
        User     = [System.Uri]::UnescapeDataString($credentials[0])
        Password = [System.Uri]::UnescapeDataString($credentials[1])
    }
}

function Set-PostgresEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Connection,
        [Parameter(Mandatory = $true)]
        [ValidateSet("require", "prefer")]
        [string]$SslMode
    )

    $env:PGHOST = $Connection.Host
    $env:PGPORT = [string]$Connection.Port
    $env:PGDATABASE = $Connection.Database
    $env:PGUSER = $Connection.User
    $env:PGPASSWORD = $Connection.Password
    $env:PGSSLMODE = $SslMode
}

$toolNames = @("pg_dump", "pg_restore", "psql")
$tools = @{}
foreach ($toolName in $toolNames) {
    $tool = Get-Command $toolName -ErrorAction SilentlyContinue
    if (-not $tool) {
        throw "$toolName was not found. Install PostgreSQL command-line tools and retry."
    }
    $tools[$toolName] = $tool.Source
}

$envFile = Join-Path $PSScriptRoot "..\backend\.env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing backend\.env. Configure its local DATABASE_URL before migrating."
}

$databaseLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match "^\s*DATABASE_URL=" } |
    Select-Object -First 1
if (-not $databaseLine) {
    throw "backend\.env does not define DATABASE_URL."
}

$sourceUrl = ($databaseLine -replace "^\s*DATABASE_URL=", "").Trim().Trim('"').Trim("'")
$source = Get-PostgresConnectionParts -ConnectionString $sourceUrl -ParameterName "Local DATABASE_URL"
if ($source.Host -notin @("localhost", "127.0.0.1", "::1")) {
    throw "The local DATABASE_URL does not point to localhost; refusing to dump a different database."
}

Write-Host "Paste the Neon direct PostgreSQL connection URL at the hidden prompt. It will not be displayed."
$secureTargetUrl = Read-Host "Neon connection URL" -AsSecureString
$targetUrlPointer = [IntPtr]::Zero
$targetUrl = $null
$dumpPath = Join-Path ([System.IO.Path]::GetTempPath()) ("connextionz-neon-" + [guid]::NewGuid().ToString("N") + ".dump")
$pgEnvironmentNames = @("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")
$previousPgEnvironment = @{}
foreach ($name in $pgEnvironmentNames) {
    $previousPgEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    $targetUrlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureTargetUrl)
    $targetUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($targetUrlPointer)
    $target = Get-PostgresConnectionParts -ConnectionString $targetUrl -ParameterName "Neon URL"
    if ($target.Host -notlike "*.neon.tech" -or $target.Host -match "-pooler(?:\.|$)") {
        throw "Use the direct Neon host shown in the Connect panel, not a pooled host."
    }

    Set-PostgresEnvironment -Connection $target -SslMode "require"
    $tableCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%';"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not connect to the Neon database. Check that the URL is correct and Neon allows connections."
    }

    $tableCount = 0
    if (-not [int]::TryParse(($tableCountText | Out-String).Trim(), [ref]$tableCount)) {
        throw "Could not verify whether the Neon database is empty."
    }
    if ($tableCount -ne 0) {
        throw "The Neon database already has user tables. No data was changed; choose an empty database to avoid overwriting data."
    }

    Write-Host "Exporting the local database to a temporary file..."
    Set-PostgresEnvironment -Connection $source -SslMode "prefer"
    $sourceUserCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not verify accounts in the local database. Neon was not changed."
    }
    $sourceUserCount = 0
    if (-not [int]::TryParse(($sourceUserCountText | Out-String).Trim(), [ref]$sourceUserCount)) {
        throw "Could not read the local account count. Neon was not changed."
    }
    if ($sourceUserCount -eq 0) {
        throw "The local database contains no user accounts. Neon was not changed; verify this is the database used before."
    }

    & $tools["pg_dump"] --format=custom --no-owner --no-acl --file=$dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "The local database export failed. Neon was not changed."
    }

    Write-Host "Importing into the empty Neon database..."
    Set-PostgresEnvironment -Connection $target -SslMode "require"
    & $tools["pg_restore"] --no-owner --no-acl --exit-on-error --single-transaction "--dbname=$($target.Database)" $dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "The import failed. The local database was not changed; a dump was retained at $dumpPath."
    }

    $userCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    if ($LASTEXITCODE -ne 0) {
        throw "The database was imported, but the users table could not be verified. A dump was retained at $dumpPath."
    }

    $userCount = 0
    if (-not [int]::TryParse(($userCountText | Out-String).Trim(), [ref]$userCount)) {
        throw "The database was imported, but the user count could not be read. A dump was retained at $dumpPath."
    }
    if ($userCount -ne $sourceUserCount) {
        throw "The import completed, but the account count does not match the local database. A dump was retained at $dumpPath."
    }

    Remove-Item -LiteralPath $dumpPath -Force
    Write-Host "Migration complete. Neon now contains $userCount user account(s). The local database was not changed."
    Write-Host "In Vercel, set Production DATABASE_URL to this Neon URL, changing its prefix to postgresql+asyncpg://, then redeploy."
}
finally {
    if ($targetUrlPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($targetUrlPointer)
    }
    $targetUrl = $null
    if ($null -ne $secureTargetUrl) {
        $secureTargetUrl.Dispose()
    }
    foreach ($name in $pgEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousPgEnvironment[$name], "Process")
    }
    if (Test-Path -LiteralPath $dumpPath) {
        Write-Host "A temporary database dump remains at $dumpPath. Remove it after resolving the migration result."
    }
}
