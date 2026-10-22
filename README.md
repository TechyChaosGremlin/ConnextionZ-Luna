
  # Untitled

  This is a code bundle for Untitled. The original project is available at https://www.figma.com/design/LpKTpaLspGZ1SVvPu1bdUU/Untitled.

  ## Running the code

  Run `npm i` to install the dependencies.

  Run `npm run dev` to start the development server.

  ## Post identifiers and social state

  The active API (`backend/app/main.py` and `backend/api/graphql.py`) uses
  database UUID primary keys, generated as UUIDv7 for new posts. Feed, search,
  profile, and create-post responses return that same ID. Keep it unchanged
  for comments, likes, saves, shares, updates, deletion, and watch tracking;
  their GraphQL arguments remain `UUID!`. The retired `backend/app_legacy`
  models and frontend prototype fixtures use different identifiers and must
  not supply IDs to the live API.

  A failed feed request now shows an error and Retry, rather than substituting
  prototype posts such as `"3"`. An empty API feed stays empty. Search also
  reports post-query failures instead of returning demo posts.

  Likes and follows require server confirmation; missing/expired sessions are
  errors, not successful local-only changes. Shared viewer-scoped stores apply
  mutation results, roll back failed optimistic writes, reject duplicate
  in-flight clicks, and ignore reads started before a newer mutation or account
  change. Fresh feed/search/profile reads hydrate `isLiked` and `isFollowing`
  from the backend, including after reload. API creator identities are keyed by
  their returned profile ID, never by a matching demo username.

  Run `npm run test:social` for the ID/state regressions or
  `npm run test:frontend` for the full frontend test set.

  ## Profile and Settings navigation

  The app uses local screen state in `src/app/App.tsx`, not URL-based routes.
  The Profile navigation item opens `OwnProfileScreen` inside the authenticated
  `SessionProvider`. Its identity, counts, posts, and playlists come from the
  GraphQL `me` response; failed loads show Retry instead of a demo profile.

  Settings is a separate `settings` screen, opened by the profile's Settings
  button (also available while the profile is loading or unavailable).
  Edit Profile and the profile-avatar edit shortcut open the existing
  `editProfile` Settings subroute. Settings Back and its profile card return
  to the creator profile; the explicit Edit Profile settings row stays intact.
  No database or deployment configuration changes are required.

  Run `npm run test:profile-navigation` for navigation and backend-identity
  regressions.

  ## Post media uploads

  The composer creates a backend draft, uploads the original file through
  `POST /media/posts/{postId}`, then publishes that same post ID. Videos also
  upload a JPEG thumbnail with multipart `role=thumbnail`; image posts use the
  original image for their thumbnail. Failed uploads remain unpublished drafts
  and show an error. Retrying in the composer reuses its draft ID.

  Uploads persist the media storage key and MIME type, plus stable
  `/media/{mediaId}/content` references on the post. Profile, feed, and post
  queries return those references and `mediaType`. Older attached media rows
  with missing post references are resolved on reads without a database backfill.
  Local caching retains stable references, never blob URLs or signed URLs.

  The existing S3-compatible configuration supplies the bucket name and endpoint;
  the bucket does not need to be public. Media renderers request
  `GET /media/{mediaId}/url` with the session bearer token to obtain a private,
  five-minute signed download URL. URLs are resolved again on mount/reload and
  once automatically on a media load error; persistent failures show an error.
  Image posts render as images, not video elements.

  Creator profiles do not expose a media-repair action or dialog. Missing media
  remains explicitly unavailable; post counts and metadata are not evidence that
  a storage object can be retrieved. The existing repair backend and standalone
  repair components are retained, but are not connected to the profile.

  The three investigated posts have no associated media rows, and no reliable
  mapping to their original storage objects has been established. Original object
  existence remains unverified. An authorized operator must first establish an
  authoritative post-to-object mapping from trusted records/backups before any
  approved restoration. Do not guess keys from filenames or timestamps.
  Alternatively, the creator must supply each post's intended video and a JPEG
  poster (up to 8MB) for an explicitly approved authenticated upload to that same
  existing post UUID: primary video first, then thumbnail bound to its returned
  media ID. This uploads new copies, not recovery of unidentified originals.
  Neither path is automatic, and no production recovery has been verified.

  Repair reuses the authenticated multipart endpoint with `repair_missing=true`.
  Normal and repair uploads both check missing-slot preconditions, upload outside
  the row lock, then lock/refetch the post and recheck ownership, media references
  and missing-slot preconditions before attaching. Competing requests return
  HTTP 409 and clean up only their own staged object; existing media is never
  replaced by this endpoint. Primary uploads require no active media rows or
  post media references. Thumbnail repair sends `expected_primary_id`, the
  confirmed video's media UUID, and requires that same associated video and an
  empty thumbnail both before storage I/O and under the final lock. Byte validation
  requires video for primary repair and JPEG for thumbnail repair.
  The normal composer keeps a random `upload_id` per file in the current draft
  and verifies it through the owner/admin-only
  `GET /media/posts/{post_id}/uploads/{upload_id}?role=primary|thumbnail` receipt
  endpoint before retrying. A receipt confirms the exact active media row and
  current post slot; thumbnail receipts also check the confirmed primary ID.
  Missing receipts permit retry with the same identity, not adoption of another
  upload. Every attempt stages a separate object key, so even concurrent attempts
  with the same receipt ID cannot overwrite each other's bytes. Confirmed
  attachments are retained across thumbnail/publish retries. Publication sends
  those confirmed media IDs; the server checks them under the same post lock
  before changing status, so a deleted/replaced slot cannot be published instead.
  Identities live only in the open composer; a reload does not automatically
  resume a draft. Normal thumbnail uploads also require an active primary.
  The repair dialog still fails closed on a lost response and requires
  close/reload/review before continuing.
  The two files commit separately: a failed JPEG upload leaves the video attached.
  Retry keeps confirmed uploads and checks the authenticated profile before sending
  only the missing file. If a response was lost or another upload won the race,
  the guard rejects overwriting; close/reload and review the post before continuing.
  Upload completion is verified by re-reading the same post. Repair is limited
  to posts returned by the current profile query and cannot identify bucket
  objects or replace existing media.

  No database reservation or migration is used. Concurrent attempts can stage
  separate objects before one loses the final precondition check. Failed cleanup
  is reported explicitly and may require operator review. The focused interleaving
  unit tests simulate row locking and storage. Separate opt-in PostgreSQL tests
  exercise real row locks and transaction visibility with storage still mocked.

  Failure before commit is rolled back before staged-object cleanup. Once commit
  has been attempted, any exception is treated conservatively as an uncertain
  outcome: the object is retained and HTTP 503 requests verification/retry. A
  rollback after a missing commit acknowledgement is not proof that commit failed.
  This can leave an orphan, but cannot intentionally delete possibly committed media.

  Individual media deletion and post/account deletion acquire the post locks,
  detach references and soft-delete media in the database, then clean storage only
  after a confirmed commit. Delete a video's thumbnail before its primary video;
  otherwise individual primary deletion returns HTTP 409. Concurrent thumbnail
  repair either wins that lock (and blocks primary deletion) or detects the deleted
  primary before attaching. Other posts' live URL references block deletion;
  shared storage objects are retained while another active row/reference needs them.
  A failed individual cleanup leaves a tombstone so the same media DELETE can
  retry. Cleanup failures after whole-post/account deletion may require operator
  review. No object keys are returned by the receipt or error responses.

  Run `npm run test:media` and
  `.venv\Scripts\python.exe -m pytest backend/tests/test_media_upload.py backend/tests/test_post_media_pipeline.py`
  for focused regressions. These tests mock storage and database repositories;
  they do not upload to the live bucket or modify production data. The existing
  live-storage smoke test remains opt-in for an explicitly approved nonproduction
  target. No bucket-policy, environment-variable, or database-schema changes
  are required for this fix.

  For real PostgreSQL media race tests, install local PostgreSQL binaries with
  `initdb` and `pg_ctl` on PATH, then run in PowerShell:

  ```powershell
  $env:MEDIA_TEST_LOCAL_POSTGRES = "1"
  & .\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -c backend\pyproject.toml -o "addopts=" backend\tests\test_media_postgres.py -q
  Remove-Item Env:\MEDIA_TEST_LOCAL_POSTGRES
  ```

  This test-only fixture initializes a fresh temporary cluster with generated
  credentials on a loopback-only ephemeral port. It never consumes a database URL
  from application settings or test environment variables. Each case creates and
  drops its own schema; the cluster is stopped and its temporary files removed
  afterward. No migrations, real accounts, or remote storage are used.
  The tests run the production repositories and independent SQLAlchemy sessions
  using READ COMMITTED, `autoflush=False`, and `expire_on_commit=False`. They
  observe PostgreSQL lock waits with `pg_blocking_pids` and verify committed rows
  from independent sessions. Coverage includes normal/repair slot contention,
  thumbnail repair versus primary deletion in both orders, live-reference
  preservation, and reattachment while old-object cleanup is paused.
  Commit-acknowledgement loss is injected before/after a real commit: this checks
  the handler's conservative cleanup behavior, not actual network fault recovery.
  Hosted pooler behavior and live storage remain outside these tests.

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

  