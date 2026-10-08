
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
  local-only; production requirements and deployment configuration are unchanged.
  