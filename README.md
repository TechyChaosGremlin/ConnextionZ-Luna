
  # Untitled

  This is a code bundle for Untitled. The original project is available at https://www.figma.com/design/LpKTpaLspGZ1SVvPu1bdUU/Untitled.

  ## Running the code

  Run `npm i` to install the dependencies.

  Run `npm run dev` to start the development server.

  ## Local registration setup

  Use `http://localhost:5173` for the frontend, not the hosted site.
  In `.env.development.local` (ignored by Git), set:

  ```dotenv
  VITE_API_URL=http://127.0.0.1:8002
  ```

  Restart Vite after changing this value. From the repository root, install the
  local-only backend requirements using the existing virtualenv:

  ```powershell
  & ".\.venv\Scripts\python.exe" -m pip install -r .\backend\requirements-local.txt
  $env:DEBUG = "false"
  $env:ENVIRONMENT = "development"
  & ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8002 --reload
  ```

  Keep the local PostgreSQL connection in `backend\.env`. In another terminal,
  run `npm run dev` and open `http://localhost:5173`. Health is available at
  `http://127.0.0.1:8002/health`.

  Registration uses `POST /auth/register` with the existing `email`, `username`,
  and `password` query parameters, then `POST /auth/login` and a GraphQL `me`
  request to load the account. FastAPI CORS already allows `http://localhost:5173`.
  The authenticated `me` request also requires the existing token-revocation
  Redis service at `localhost:6379` (or the `REDIS_URL` in `backend\.env`).
  Without it, account creation and password login can succeed, but the profile
  request returns `Authentication required`; the frontend cannot finish signing
  in. Do not bypass the revocation check to work around a missing local service.
  On this Windows setup Redis runs in the existing Ubuntu WSL instance. To
  start it and keep the WSL instance alive during development, leave this command
  running in a separate terminal (or run the `Local Redis (WSL)` VS Code task):

  ```powershell
  wsl -d Ubuntu -u root -- sh -lc 'service redis-server start && redis-cli ping && exec sleep infinity'
  ```

  The expected response is `PONG`; keep Redis bound to loopback. A systemd
  service alone does not keep an otherwise idle WSL instance running.
  The local requirements pin Strawberry and graphql-core to compatible versions:
  graphql-core 3.3 changes parser/execution behavior used by the existing query
  limiter and can break the post-registration profile request. These pins are
  local-only and do not affect production dependencies.

  ## Deploying the frontend and API to Vercel

  The root `vercel.json` configures Vercel Services so the Vite frontend and
  FastAPI backend deploy together in the same Vercel project and share one
  domain. Keep the Vercel project's **Root Directory** set to the repository
  root. Requests to `/auth/*`, `/graphql`, `/api/*`, `/media/*`, and `/health`
  route to the backend; other paths serve the frontend.

  Add these environment variables to the Vercel project for **Production,
  Preview, and Development** as needed:

  - `ENVIRONMENT=production` and `DEBUG=false`
  - A unique `JWT_SECRET_KEY` with at least 32 characters
  - Production `DATABASE_URL`. With the default `INFRASTRUCTURE_MODE=full`,
    also provide `REDIS_URL` and `RABBITMQ_URL`; both services must be reachable
    during startup. The PostgreSQL-only reduced beta below does not require them.

  To copy the local PostgreSQL database into a new, empty Neon database, run
  `.\scripts\migrate-local-db-to-neon.ps1` in PowerShell from the repository
  root. If Neon already has the app's schema and you want to keep its tables,
  run `.\scripts\migrate-local-db-to-neon.ps1 -AccountsOnly` instead. That mode
  appends only local `users` and `profiles` rows in one transaction; unique-key
  conflicts or schema mismatches abort the import without overwriting existing
  Neon rows. Either mode reads the local source URL from `backend\.env` and
  prompts for the Neon direct connection URL without displaying it. Add
  `-UseClipboard` to use the copied connection URL instead of the hidden prompt.
  Both modes leave the local database unchanged.

  After a successful copy, set Vercel's Production `DATABASE_URL` to the same
  Neon connection URL, changing the scheme prefix from `postgresql://` to
  `postgresql+asyncpg://`, then redeploy.

  To move the current Neon database to a fresh Supabase project, run
  `.\scripts\migrate-neon-to-supabase.ps1` from the repository root. The script
  prompts privately for both database URLs, creates the app schema in Supabase
  using the repository's Alembic migrations, and imports Neon `public` data in
  one transaction. It leaves Neon unchanged, preserves Supabase-managed
  schemas, and excludes the migration version marker and migration-seeded
  categories. Use Supabase's direct connection URL or its session pooler on
  port 5432; do not use the transaction pooler. The Supabase `public` schema
  must be empty before starting.

  Configure the app's `DATABASE_URL` with the Supabase connection URL using the
  `postgresql+asyncpg://` scheme. For Vercel/serverless deployments, use the
  transaction pooler (port 6543); the backend disables asyncpg's statement
  cache and application-side pool for that mode. For persistent deployments,
  use the direct connection or session pooler (port 5432). Keep
  `DATABASE_URL_SYNC` on the `postgresql+psycopg://` scheme and use a direct
  or session-pooler URL for Alembic migrations; do not run migrations through
  the transaction pooler. Supabase SSL is required automatically unless the
  URL specifies its own SSL setting.

  After the script reports success, set Vercel's Production `DATABASE_URL` to
  the Supabase transaction-pooler URL, then redeploy. Keep the Neon project
  until sign-in and the deployed app have been verified against Supabase.

  Once deployed, `/health` checks backend reachability. The frontend uses
  same-origin API URLs automatically in production, so it does not rely on a
  `VITE_API_URL` value. Set `CORS_ORIGINS` only if the API must also accept
  requests from another origin; use a JSON list of exact origins.

  ### PostgreSQL-only reduced beta

  Set `INFRASTRUCTURE_MODE=postgres_beta` to run without Redis or RabbitMQ.
  Keep `ENVIRONMENT=production`, `DEBUG=false`, a private `JWT_SECRET_KEY`
  of at least 32 characters, `DATABASE_URL`, and JSON `CORS_ORIGINS` configured.
  Apply Alembic migration 218 using the direct/session `DATABASE_URL_SYNC`
  before deploying this mode. It adds the persistent `token_revocations` table;
  startup and `/health/ready` fail if that table cannot be queried. Logout
  commits revocations before reporting success, and authentication fails closed
  if the store is unavailable. Expired revocations are cleaned up on logout.

  This mode disables broker-backed background queues and their dead-letter
  delivery; attempts to connect to RabbitMQ report an explicit error. The
  current HTTP/GraphQL routes do not enqueue broker jobs. This does not provide
  a durable worker or promise that streaming/background tasks survive Vercel
  instance shutdown. Existing per-instance rate limits are unchanged and are
  not a distributed abuse-control system.

  If switching an existing Redis-backed deployment to this mode, rotate the
  JWT signing secret to invalidate outstanding tokens: Redis blacklist entries
  are not copied into PostgreSQL. `INFRASTRUCTURE_MODE=full` remains the default
  and continues to require Redis and RabbitMQ.

  