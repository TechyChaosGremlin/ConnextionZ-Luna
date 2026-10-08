param(
    [switch]$CheckClipboard,
    [switch]$UseClipboard,
    [switch]$AccountsOnly
)

$ErrorActionPreference = "Stop"

function Get-PostgresConnectionParts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConnectionString,
        [Parameter(Mandatory = $true)]
        [string]$ParameterName
    )

    $normalizedConnectionString = $ConnectionString.Trim()
    $urlPattern = "(?i)(?:postgres|postgresql)(?:\+asyncpg|\+psycopg)?://[^\s'""<>]+"
    $urlToken = [regex]::Match($normalizedConnectionString, $urlPattern)
    if ($urlToken.Success) {
        $normalizedConnectionString = $urlToken.Value.TrimEnd([char[]]@("'", '"', ";", ",", ")"))
    }

    $uri = $null
    if (-not [System.Uri]::TryCreate($normalizedConnectionString, [System.UriKind]::Absolute, [ref]$uri)) {
        throw "$ParameterName is not a valid PostgreSQL URL. Copy the full direct connection string; URL-special characters in the password must be percent-encoded."
    }

    $scheme = $uri.Scheme.ToLowerInvariant()
    if ($scheme -notin @("postgresql", "postgres", "postgresql+asyncpg", "postgresql+psycopg")) {
        throw "$ParameterName must use a PostgreSQL URL."
    }

    if ([string]::IsNullOrWhiteSpace($uri.Host) -or [string]::IsNullOrWhiteSpace($uri.AbsolutePath.TrimStart("/")) -or -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw "$ParameterName must include a host and database name and must not contain a URL fragment."
    }

    $credentials = $uri.UserInfo.Split(":", 2)
    if ($credentials.Count -ne 2 -or [string]::IsNullOrWhiteSpace($credentials[0])) {
        throw "$ParameterName must include a database username and password."
    }

    $databaseName = [System.Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart("/"))
    if ([string]::IsNullOrWhiteSpace($databaseName)) {
        throw "$ParameterName must include a database name."
    }

    $port = if ($uri.IsDefaultPort) { 5432 } else { $uri.Port }
    if ($port -lt 1 -or $port -gt 65535) {
        throw "$ParameterName contains an invalid PostgreSQL port."
    }

    return [PSCustomObject]@{
        Host     = $uri.Host
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

if ($CheckClipboard) {
    try {
        $null = Get-PostgresConnectionParts -ConnectionString (Get-Clipboard -Raw) -ParameterName "Clipboard URL"
        Write-Output "Parser check passed."
    }
    catch {
        Write-Output ("Parser check failed: " + $_.Exception.Message)
        exit 1
    }
    exit 0
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

$secureTargetUrl = $null
$targetUrlPointer = [IntPtr]::Zero
$targetUrl = $null
if ($UseClipboard) {
    $targetUrl = Get-Clipboard -Raw
    if ([string]::IsNullOrWhiteSpace($targetUrl)) {
        throw "The clipboard is empty. Copy the Neon direct connection URL, then retry."
    }
}
else {
    Write-Host "Paste the Neon direct PostgreSQL connection URL at the hidden prompt. It will not be displayed."
    $secureTargetUrl = Read-Host "Neon connection URL" -AsSecureString
}

$dumpPath = Join-Path ([System.IO.Path]::GetTempPath()) ("connextionz-neon-" + [guid]::NewGuid().ToString("N") + ".dump")
$pgEnvironmentNames = @("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")
$previousPgEnvironment = @{}
foreach ($name in $pgEnvironmentNames) {
    $previousPgEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    if ($null -ne $secureTargetUrl) {
        $targetUrlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureTargetUrl)
        $targetUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($targetUrlPointer)
    }
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
    $targetUserCount = 0L
    $targetProfileCount = 0L
    if ($AccountsOnly) {
        $accountTablesExist = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT (to_regclass('public.users') IS NOT NULL AND to_regclass('public.profiles') IS NOT NULL);"
        if ($LASTEXITCODE -ne 0 -or ($accountTablesExist | Out-String).Trim() -ne "t") {
            throw "The Neon database must already have public.users and public.profiles tables. No data was changed."
        }

        $targetUserCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
        if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetUserCountText | Out-String).Trim(), [ref]$targetUserCount)) {
            throw "Could not verify the existing Neon account count. No data was changed."
        }

        $targetProfileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
        if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetProfileCountText | Out-String).Trim(), [ref]$targetProfileCount)) {
            throw "Could not verify the existing Neon profile count. No data was changed."
        }
    }
    elseif ($tableCount -ne 0) {
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

    $dumpArguments = @("--format=custom", "--no-owner", "--no-acl", "--file=$dumpPath")
    if ($AccountsOnly) {
        $sourceProfileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
        $sourceProfileCount = 0L
        if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($sourceProfileCountText | Out-String).Trim(), [ref]$sourceProfileCount)) {
            throw "Could not verify local profiles. Neon was not changed."
        }
        $dumpArguments += @("--data-only", "--table=public.users", "--table=public.profiles")
    }
    & $tools["pg_dump"] @dumpArguments
    if ($LASTEXITCODE -ne 0) {
        throw "The local database export failed. Neon was not changed."
    }

    if ($AccountsOnly) {
        Write-Host "Importing local users and profiles into the existing Neon tables..."
    }
    else {
        Write-Host "Importing into the empty Neon database..."
    }
    Set-PostgresEnvironment -Connection $target -SslMode "require"
    $restoreArguments = @("--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", "--dbname=$($target.Database)")
    if ($AccountsOnly) {
        $restoreArguments += "--data-only"
    }
    & $tools["pg_restore"] @restoreArguments $dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "The import failed. The local database was not changed; Neon changes were rolled back. A dump was retained at $dumpPath."
    }

    $userCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    if ($LASTEXITCODE -ne 0) {
        throw "The database was imported, but the users table could not be verified. A dump was retained at $dumpPath."
    }

    $userCount = 0
    if (-not [int]::TryParse(($userCountText | Out-String).Trim(), [ref]$userCount)) {
        throw "The database was imported, but the user count could not be read. A dump was retained at $dumpPath."
    }
    if ($AccountsOnly) {
        if ($userCount -ne ($targetUserCount + $sourceUserCount)) {
            throw "The import completed, but the Neon account count did not increase by the expected number. A dump was retained at $dumpPath."
        }
        $profileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
        $profileCount = 0L
        if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($profileCountText | Out-String).Trim(), [ref]$profileCount)) {
            throw "The users and profiles were imported, but the final profile count could not be verified. A dump was retained at $dumpPath."
        }
        if ($profileCount -ne ($targetProfileCount + $sourceProfileCount)) {
            throw "The import completed, but the Neon profile count did not increase by the expected number. A dump was retained at $dumpPath."
        }
    }
    elseif ($userCount -ne $sourceUserCount) {
        throw "The import completed, but the account count does not match the local database. A dump was retained at $dumpPath."
    }

    Remove-Item -LiteralPath $dumpPath -Force
    if ($AccountsOnly) {
        Write-Host "Migration complete. Imported $sourceUserCount user account(s) and $sourceProfileCount profile(s) into the existing Neon database. The local database and existing Neon rows were not overwritten."
    }
    else {
        Write-Host "Migration complete. Neon now contains $userCount user account(s). The local database was not changed."
    }
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
