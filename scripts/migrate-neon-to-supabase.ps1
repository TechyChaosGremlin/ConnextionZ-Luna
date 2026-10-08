param()

$ErrorActionPreference = "Stop"

function Get-PostgresConnectionParts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConnectionString,
        [Parameter(Mandatory = $true)]
        [string]$ParameterName
    )

    $value = $ConnectionString.Trim()
    $urlMatch = [regex]::Match($value, "(?i)(?:postgres|postgresql)://[^\s'""<>]+")
    if ($urlMatch.Success) {
        $value = $urlMatch.Value.TrimEnd([char[]]@("'", '"', ";", ",", ")"))
    }

    $uri = $null
    if (-not [System.Uri]::TryCreate($value, [System.UriKind]::Absolute, [ref]$uri)) {
        throw "$ParameterName is not a valid PostgreSQL URL. Copy the complete connection URL."
    }
    if ($uri.Scheme.ToLowerInvariant() -notin @("postgres", "postgresql") -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        [string]::IsNullOrWhiteSpace($uri.AbsolutePath.TrimStart("/")) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw "$ParameterName must be a PostgreSQL URL with a host and database name."
    }

    $credentials = $uri.UserInfo.Split(":", 2)
    if ($credentials.Count -ne 2 -or [string]::IsNullOrWhiteSpace($credentials[0])) {
        throw "$ParameterName must include a database username and password."
    }

    $port = if ($uri.IsDefaultPort) { 5432 } else { $uri.Port }
    if ($port -lt 1 -or $port -gt 65535) {
        throw "$ParameterName contains an invalid PostgreSQL port."
    }

    return [PSCustomObject]@{
        Host     = $uri.Host
        Port     = $port
        Database = [System.Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart("/"))
        User     = [System.Uri]::UnescapeDataString($credentials[0])
        Password = [System.Uri]::UnescapeDataString($credentials[1])
        Url      = $value
    }
}

function Read-SecretUrl {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secureUrl = Read-Host $Prompt -AsSecureString
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureUrl)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $secureUrl.Dispose()
    }
}

function Set-PostgresEnvironment {
    param([Parameter(Mandatory = $true)][PSCustomObject]$Connection)

    $env:PGHOST = $Connection.Host
    $env:PGPORT = [string]$Connection.Port
    $env:PGDATABASE = $Connection.Database
    $env:PGUSER = $Connection.User
    $env:PGPASSWORD = $Connection.Password
    $env:PGSSLMODE = "require"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtualenv Python was not found at .venv\Scripts\python.exe. Create/restore the project environment first."
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

$sourceUrl = Read-SecretUrl "Neon direct connection URL (input hidden)"
$targetUrl = Read-SecretUrl "Supabase direct or session-pooler URL (input hidden)"
$source = Get-PostgresConnectionParts -ConnectionString $sourceUrl -ParameterName "Neon URL"
$target = Get-PostgresConnectionParts -ConnectionString $targetUrl -ParameterName "Supabase URL"
$sourceUrl = $null
$targetUrl = $null

if ($source.Host -notlike "*.neon.tech" -or $source.Host -match "-pooler(?:\.|$)") {
    throw "The source must be the direct Neon host, not a pooled host."
}
if ($target.Host -notlike "*.supabase.co" -and $target.Host -notlike "*.pooler.supabase.com") {
    throw "The target host must be a Supabase database or Supabase session-pooler host."
}
if ($target.Host -like "*.pooler.supabase.com" -and $target.Port -ne 5432) {
    throw "Use Supabase's session pooler on port 5432, not its transaction pooler."
}

$dumpPath = Join-Path ([System.IO.Path]::GetTempPath()) ("connextionz-supabase-" + [guid]::NewGuid().ToString("N") + ".dump")
$pgEnvironmentNames = @("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")
$previousPgEnvironment = @{}
foreach ($name in $pgEnvironmentNames) {
    $previousPgEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$previousDatabaseUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
$previousDatabaseUrlSync = [Environment]::GetEnvironmentVariable("DATABASE_URL_SYNC", "Process")

try {
    Set-PostgresEnvironment -Connection $target
    $targetTableCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e');"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not connect to Supabase. Check the target URL and network access."
    }
    $targetTableCount = 0
    if (-not [int]::TryParse(($targetTableCountText | Out-String).Trim(), [ref]$targetTableCount)) {
        throw "Could not verify that the Supabase public schema is empty."
    }
    if ($targetTableCount -ne 0) {
        throw "Supabase public already contains tables. No data was changed; use a fresh Supabase project."
    }

    Set-PostgresEnvironment -Connection $source
    $sourceTablesExist = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT (to_regclass('public.users') IS NOT NULL AND to_regclass('public.profiles') IS NOT NULL);"
    if ($LASTEXITCODE -ne 0 -or ($sourceTablesExist | Out-String).Trim() -ne "t") {
        throw "The Neon database must contain public.users and public.profiles."
    }
    $sourceUserCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    $sourceUserCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($sourceUserCountText | Out-String).Trim(), [ref]$sourceUserCount) -or $sourceUserCount -eq 0) {
        throw "Could not verify accounts in Neon, or Neon contains no users. Supabase was not changed."
    }
    $sourceProfileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
    $sourceProfileCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($sourceProfileCountText | Out-String).Trim(), [ref]$sourceProfileCount)) {
        throw "Could not verify profiles in Neon. Supabase was not changed."
    }

    $sourceVersionText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT version_num FROM public.alembic_version LIMIT 1;"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($sourceVersionText | Out-String).Trim())) {
        throw "Could not read Neon schema version. Supabase was not changed."
    }

    $targetAsyncUrl = $target.Url -replace "^(?i:postgresql|postgres)://", "postgresql+asyncpg://"
    $targetSyncUrl = $target.Url -replace "^(?i:postgresql|postgres)://", "postgresql+psycopg://"
    $env:DATABASE_URL = $targetAsyncUrl
    $env:DATABASE_URL_SYNC = $targetSyncUrl

    Write-Host "Creating the Supabase app schema from the project's Alembic migrations..."
    Push-Location $repoRoot
    try {
        & $python -m alembic -c .\backend\alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the app schema in Supabase. No Neon data was imported."
        }
    }
    finally {
        Pop-Location
        $targetAsyncUrl = $null
        $targetSyncUrl = $null
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:DATABASE_URL_SYNC -ErrorAction SilentlyContinue
        if ($null -ne $previousDatabaseUrl) { $env:DATABASE_URL = $previousDatabaseUrl }
        if ($null -ne $previousDatabaseUrlSync) { $env:DATABASE_URL_SYNC = $previousDatabaseUrlSync }
    }

    Set-PostgresEnvironment -Connection $target
    $targetUserCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    $targetUserCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetUserCountText | Out-String).Trim(), [ref]$targetUserCount) -or $targetUserCount -ne 0) {
        throw "The new Supabase schema is not empty of users. No Neon data was imported."
    }

    $targetProfileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
    $targetProfileCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetProfileCountText | Out-String).Trim(), [ref]$targetProfileCount) -or $targetProfileCount -ne 0) {
        throw "The new Supabase schema is not empty of profiles. No Neon data was imported."
    }

    Set-PostgresEnvironment -Connection $source
    Write-Host "Exporting Neon application data..."
    & $tools["pg_dump"] --format=custom --data-only --schema=public --exclude-table=public.alembic_version --exclude-table=public.categories --no-owner --no-acl --file=$dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not export Neon data. Supabase schema was created, but no Neon data was imported."
    }

    Set-PostgresEnvironment -Connection $target
    Write-Host "Importing Neon application data into Supabase in one transaction..."
    & $tools["pg_restore"] --data-only --no-owner --no-acl --exit-on-error --single-transaction "--dbname=$($target.Database)" $dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "Data import failed and was rolled back. The Neon database was not changed; a dump was retained at $dumpPath."
    }

    $targetUserCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.users;"
    $targetUserCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetUserCountText | Out-String).Trim(), [ref]$targetUserCount) -or $targetUserCount -ne $sourceUserCount) {
        throw "The import completed, but the Supabase user count does not match Neon. A dump was retained at $dumpPath."
    }
    $targetProfileCountText = & $tools["psql"] -X -qAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.profiles;"
    $targetProfileCount = 0L
    if ($LASTEXITCODE -ne 0 -or -not [long]::TryParse(($targetProfileCountText | Out-String).Trim(), [ref]$targetProfileCount) -or $targetProfileCount -ne $sourceProfileCount) {
        throw "The import completed, but the Supabase profile count does not match Neon. A dump was retained at $dumpPath."
    }

    Remove-Item -LiteralPath $dumpPath -Force
    Write-Host "Migration complete. Imported all public-schema data except Alembic's version marker and the migration-seeded categories."
    Write-Host "Verified $targetUserCount user(s) and $targetProfileCount profile(s). Neon was not modified."
    Write-Host "Update Vercel's Production DATABASE_URL to the Supabase URL with the postgresql+asyncpg:// scheme, then redeploy."
}
finally {
    foreach ($name in $pgEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousPgEnvironment[$name], "Process")
    }
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:DATABASE_URL_SYNC -ErrorAction SilentlyContinue
    if ($null -ne $previousDatabaseUrl) { $env:DATABASE_URL = $previousDatabaseUrl }
    if ($null -ne $previousDatabaseUrlSync) { $env:DATABASE_URL_SYNC = $previousDatabaseUrlSync }
    $source.Password = $null
    $target.Password = $null
    $source = $null
    $target = $null
    if (Test-Path -LiteralPath $dumpPath) {
        Write-Host "A temporary database dump remains at $dumpPath. Remove it after resolving the migration result."
    }
}
