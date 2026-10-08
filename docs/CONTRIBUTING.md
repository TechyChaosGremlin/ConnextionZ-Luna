# Contributing to ConnextionZ Platform

Thank you for your interest in contributing to ConnextionZ! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Workflow](#development-workflow)
4. [Coding Standards](#coding-standards)
5. [Testing Requirements](#testing-requirements)
6. [Pull Request Process](#pull-request-process)
7. [Issue Reporting](#issue-reporting)
8. [Alpha Backend QA](#alpha-backend-qa)

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct:
- Be respectful and inclusive
- Exercise consideration and empathy in your communication
- Focus on what is best for the community and the platform users
- Refrain from demeaning, discriminatory, or harassing behavior

## Getting Started

### Prerequisites

- **Python 3.11+** (Backend development)
- **Node.js 18+** (Frontend development)
- **Docker Desktop** (Local infrastructure)
- **Git** (Version control)
- **VS Code** (Recommended IDE)

### First-Time Setup

1. **Fork the repository** (if external contributor)
   ```bash
   git fork <repository-url>
   ```

2. **Clone your fork**
   ```bash
   git clone <your-fork-url>
   cd ConnextionZ/connextionz-platform
   ```

3. **Configure and start the canonical local stack**
   ```bash
   cp .env.example .env
   # Set the required secret values in .env before starting Compose.
   docker compose up --build -d
   docker compose run --rm api alembic upgrade head
   ```

4. **Install backend dependencies**
   ```bash
   cd backend
   python -m venv venv
   source venv/Scripts/activate  # Windows
   # source venv/bin/activate    # macOS/Linux
   pip install -r requirements.txt
   ```

5. **Install frontend dependencies**
   ```bash
   cd ..
   npm install
   ```

6. **Set up environment variables**
   ```bash
   cp .env.example .env.local
   # Edit .env.local with your local configuration
   ```

7. **Run the development servers**
   ```bash
   # Terminal 1: Backend
   cd app
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   
   # Terminal 2: Frontend
   npm start
   ```

## Development Workflow

### Branch Naming Convention

- **Feature branches**: `feature/description` (e.g., `feature/collaboration-marketplace`)
- **Bug fix branches**: `fix/description` (e.g., `fix/login-redirect-issue`)
- **Documentation branches**: `docs/description` (e.g., `docs/api-endpoint-examples`)
- **Refactor branches**: `refactor/description` (e.g., `refactor/repository-layer`)

### Commit Message Format

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Build process or auxiliary tool changes

**Examples:**
```bash
feat(auth): add two-factor authentication support

Implemented TOTP-based 2FA using pyotp library.
Added QR code generation for authenticator apps.
Updated user model with 2FA fields.

Closes #123
```

### Keeping Your Branch Updated

```bash
# Add upstream remote (if forked)
git remote add upstream <original-repo-url>

# Fetch latest changes
git fetch upstream

# Rebase your branch
git checkout feature/your-feature
git rebase upstream/main
```

## Coding Standards

### Python (Backend)

- Follow **PEP 8** style guide
- Use **type hints** for all function signatures
- Write **docstrings** for public functions and classes
- Use **Black** for code formatting (auto-formatter)
- Use **Flake8** for linting
- Use **MyPy** for static type checking

**Example:**
```python
from typing import List, Optional
from fastapi import APIRouter, Depends
from .repositories import UserRepository
from .models import User

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int,
    user_repo: UserRepository = Depends()
) -> User:
    """
    Retrieve a user by their ID.
    
    Args:
        user_id: The unique identifier of the user
        user_repo: Injected user repository dependency
        
    Returns:
        User object if found
        
    Raises:
        HTTPException: If user not found (404)
    """
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

### TypeScript/React (Frontend)

- Use **TypeScript** for all new code (no `any` types)
- Follow **Airbnb JavaScript Style Guide**
- Use **ESLint** and **Prettier** for code quality
- Use **functional components** with hooks (avoid class components)
- Use **named exports** for components

**Example:**
```typescript
import React, { useState, useEffect } from 'react';
import { User } from '../types/User';
import { userService } from '../services/userService';

interface UserProfileProps {
  userId: string;
  onUpdate?: (user: User) => void;
}

export const UserProfile: React.FC<UserProfileProps> = ({ 
  userId, 
  onUpdate 
}) => {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchUser = async () => {
      try {
        setLoading(true);
        const data = await userService.getById(userId);
        setUser(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch user');
      } finally {
        setLoading(false);
      }
    };

    fetchUser();
  }, [userId]);

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!user) return <div>User not found</div>;

  return (
    <div className="user-profile">
      <h1>{user.name}</h1>
      <p>{user.bio}</p>
    </div>
  );
};
```

### GraphQL

- Use **descriptive names** for queries and mutations
- Implement **pagination** for list queries
- Use **fragments** to share field selections
- Document **all schema types** with descriptions

**Example:**
```graphql
"""
Represents a user in the ConnextionZ platform
"""
type User {
  """
  Unique identifier of the user
  """
  id: ID!
  
  """
  Display name of the user
  """
  name: String!
  
  """
  URL-friendly identifier
  """
  username: String!
  
  """
  User's biography or description
  """
  bio: String
  
  """
  List of collaborations the user has participated in
  """
  collaborations(
    """
    Number of items to return
    """
    first: Int = 10
    
    """
    Pagination cursor
    """
    after: String
  ): CollaborationConnection!
}

type Query {
  """
  Fetch a user by their username
  """
  userByUsername(username: String!): User
}
```

## Testing Requirements

For alpha backend QA, use the [focused checklist and issue register](#alpha-backend-qa)
below rather than the general full-suite examples in this section.

### Test Coverage Requirements

- **Minimum 80% code coverage** for new features
- **Unit tests** for all business logic
- **Integration tests** for API endpoints
- **E2E tests** for critical user flows

### Running Tests

**Backend:**
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_auth.py

# Run specific test
pytest tests/test_auth.py::test_login_success
```

**Frontend:**
```bash
# Run all tests
npm test

# Run with coverage
npm test -- --coverage

# Run specific test file
npm test -- UserProfile.test.tsx

# Run in watch mode
npm test -- --watch
```

### Writing Tests

**Python (pytest):**
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_get_user_success():
    """Test successful user retrieval"""
    response = client.get("/users/1")
    assert response.status_code == 200
    assert "id" in response.json()
    assert "name" in response.json()

def test_get_user_not_found():
    """Test user not found scenario"""
    response = client.get("/users/99999")
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"
```

**TypeScript (Jest + React Testing Library):**
```typescript
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { UserProfile } from './UserProfile';
import { userService } from '../services/userService';

jest.mock('../services/userService');

describe('UserProfile', () => {
  it('displays user name after loading', async () => {
    const mockUser = { id: '1', name: 'John Doe', username: 'johndoe' };
    (userService.getById as jest.Mock).mockResolvedValue(mockUser);

    render(<UserProfile userId="1" />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('John Doe')).toBeInTheDocument();
    });
  });

  it('displays error message on fetch failure', async () => {
    (userService.getById as jest.Mock).mockRejectedValue(new Error('Network error'));

    render(<UserProfile userId="1" />);

    await waitFor(() => {
      expect(screen.getByText(/Error:/)).toBeInTheDocument();
    });
  });
});
```

## Pull Request Process

### Before Creating a PR

1. **Ensure all tests pass**
   ```bash
   # Backend
   pytest --cov=app
   
   # Frontend
   npm test -- --coverage
   ```

2. **Run linters and formatters**
   ```bash
   # Backend
   black .
   flake8
   mypy .
   
   # Frontend
   npm run lint
   npm run format
   ```

3. **Update documentation** (if needed)
   - API documentation
   - README updates
   - Architecture decision records (ADRs)

4. **Rebase on latest main**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

### Creating a Pull Request

1. **Push your branch**
   ```bash
   git push origin feature/your-feature
   ```

2. **Create PR via GitHub UI**
   - Use a clear, descriptive title
   - Fill out the PR template completely
   - Link related issues
   - Add screenshots (for UI changes)

3. **PR Title Format**
   ```
   <type>(<scope>): <description>
   
   Example: feat(auth): add two-factor authentication
   ```

4. **PR Description Template**
   ```markdown
   ## Description
   Brief description of what this PR does
   
   ## Changes Made
   - Change 1
   - Change 2
   
   ## Screenshots (if applicable)
   Add screenshots here
   
   ## Testing
   Describe how you tested these changes
   
   ## Checklist
   - [ ] Tests added/updated
   - [ ] Documentation updated
   - [ ] Lint and format checks pass
   - [ ] All tests pass
   - [ ] No console errors/warnings
   
   ## Related Issues
   Closes #123
   ```

### PR Review Process

- **At least 2 approvals** required for merge
- **All CI checks** must pass
- **Address reviewer feedback** promptly
- **Squash commits** before merging (clean git history)

### After Merge

- Delete your feature branch
- Pull latest main to your local
- Celebrate your contribution! 🎉

## Issue Reporting

### Bug Reports

Use the bug-report format below when opening an issue in the repository. No
checked-in GitHub issue template currently exists. For alpha backend issues,
also include the [alpha tracking fields](#alpha-tracking-workflow).

Include:
- **Clear title** summarizing the bug
- **Steps to reproduce** (numbered list)
- **Expected behavior**
- **Actual behavior**
- **Screenshots** (if applicable)
- **Environment details** (OS, browser, etc.)
- **Relevant logs** (error messages, console output)

**Example:**
```markdown
Title: Login fails with valid credentials

## Description
When attempting to log in with correct email and password, the system returns a 500 Internal Server Error.

## Steps to Reproduce
1. Navigate to /login
2. Enter valid email: test@example.com
3. Enter valid password: password123
4. Click "Login" button

## Expected Behavior
User should be redirected to dashboard and authenticated

## Actual Behavior
Server returns 500 Internal Server Error with message "Database connection failed"

## Environment
- OS: Windows 11
- Browser: Chrome 120.0.6099.109
- Local development environment

## Logs
```
ERROR:app.auth:Database connection failed
Traceback (most recent call last):
  File "app/auth.py", line 45, in login
    user = await user_repo.get_by_email(email)
```
```

### Feature Requests

Use the **Feature Request** issue template and include:
- **Clear title** describing the feature
- **Problem statement** (what problem does this solve?)
- **Proposed solution** (how should it work?)
- **Alternatives considered** (other ways to solve the problem)
- **Additional context** (screenshots, mockups, etc.)

## Alpha Backend QA

Last reviewed: **2026-10-07 (Week 8, Task 3)**.

This is the central checked-in register for known backend alpha issues and
caveats. It extends the existing testing and issue-reporting process; it is not
a new runtime error collector or a claim that every alpha surface has passed.
Scope excludes Paid, payment/escrow, and ranking redesign.

### Existing QA and diagnostic tools

- [Backend pytest configuration](../backend/pyproject.toml) defines the focused
  test directory, strict markers/configuration, and warnings-as-errors.
- [Backend test runner](../scripts/run-backend-tests.ps1) accepts explicit
  `-PytestArgs`, but may start or reuse an API server. Mock-backed tests in the
  checklist should run directly through the project interpreter; they do not
  need a running server.
- [API standards](API_STANDARDS.md) describe error envelopes and health endpoints.
  [Exception handlers](../backend/app/errors.py) log application/unhandled
  errors; [logging middleware](../backend/app/logging_config.py) supplies
  `X-Request-ID` for request/log correlation.
- [Health endpoints](../backend/app/main.py) provide `/health` (basic service
  liveness), `/health/live` (alive), and `/health/ready` (database, Redis, RabbitMQ).
  Readiness must be checked using the JSON `status` and individual `checks`,
  not HTTP success alone. A live service is not proof of working dependencies.
- [Architecture roadmap](LEAD_ARCHITECT_TASKS.md) supplies broader hardening
  context. Its historical assessments are not this alpha register.

### Severity and classification

| Severity | Meaning and triage expectation |
|----------|--------------------------------|
| blocker | A confirmed issue prevents an essential alpha flow or validation from proceeding, with no viable workaround; resolve before alpha sign-off. |
| critical | Confirmed cross-user exposure, authorization bypass, data loss, or widespread service failure; resolve before alpha sign-off. |
| high | Confirmed failure of a core authenticated workflow, pagination integrity, or persistence; resolve before alpha sign-off unless explicitly accepted with evidence and a workaround. |
| medium | Limited reliability/performance concern or QA instability without a confirmed core application failure; assign follow-up and track evidence. |
| low | Minor diagnostic, documentation, or non-blocking usability issue; schedule normally. |

Severity describes impact, **not** reproducibility. Keep these classifications
separate:

- **Application bug:** an observed implementation failure with an expected/
  actual mismatch. Prefer a deterministic minimal regression before fixing it.
- **Test-fixture issue:** a failure caused by mocks, setup, or teardown; do not
  label the application broken merely because pytest fails.
- **Intermittent test:** inconsistent outcomes; retain both passing and failing
  evidence until a deterministic cause is identified.
- **Intentional/deferred behavior:** a documented boundary or accepted concern,
  not an invented application defect.
- **Coverage gap:** a specific required behavior with no evidence/test; not
  itself proof of an application bug.

### Alpha tracking workflow

1. Search this register and repository issues before adding a duplicate.
2. Assign a stable local ID (`ALPHA-BE-NNN`); this is not a GitHub issue number.
3. Record: title, severity, classification, status, reproducibility, affected
   surface, expected/actual behavior, minimal steps or exact test selector,
   environment/Python/database and revision, sanitized logs/request ID, owner
   (or explicitly unassigned), last-checked date, next action/acceptance criteria,
   and linked issue/PR when one exists. Never include tokens, credentials, or
   personal data.
4. Use statuses `needs-reproduction`, `confirmed`, `in-progress`, `deferred`,
   `resolved`, or `reopened`. Record skipped/dependency-blocked verification
   separately; it is not a passing result.
5. Assign an owner during triage. For a fix, capture the failing-before and
   passing-after evidence, keep the regression test, and update this register.
   Resolve application bugs only after focused verification; do not close an
   intermittent case on one isolated pass or hide a fixture warning globally.
6. Record run date, revision/worktree state, command, pass/fail/skip counts,
   warning filters, and whether the database was mocked or PostgreSQL-backed.
   Old results remain historical evidence, not a substitute for release QA.

### Known backend issues and caveats

All entries below were reviewed on 2026-10-07. Open entries are **unassigned**;
triage should assign owners before alpha sign-off. No new defect is asserted
for the intermittent or deferred entries.

| ID | Severity | Classification | Status / reproducibility | Evidence and next action |
|----|----------|----------------|--------------------------|--------------------------|
| ALPHA-BE-001 | medium | Test-fixture issue | resolved (Task 4); deterministic isolated repro before correction | [Lifecycle timestamp test](../backend/tests/test_collaboration_permissions.py) used an unrestricted `AsyncMock` session, making synchronous `db.add` asynchronous. Verified `AsyncSession.add` is synchronous; changed the double to `AsyncMock(spec=AsyncSession)` and asserted three add/flush/commit calls. Isolated lifecycle test and all 80 focused collaboration tests pass with warnings-as-errors and no warning filters. No lifecycle implementation changed. |
| ALPHA-BE-002 | medium | Test nondeterminism (formerly intermittent) | resolved (Task 4); deterministic UUID repro and corrected parameterized fixture | [For You diversity test](../backend/tests/test_following_feed.py) supplied zero authoritative engagement counts despite nonzero post counters. Scores tied; random UUIDs could consume the sole alternate creator first, leaving a four-post tail allowed by the existing diversity contract. Fixed UUIDs reproduced failure for alternate ID 10 and pass for ID 1. The fixture now supplies authoritative counts and verifies both ID orders, exact interleaving, and no dropped posts. Both isolated variants and the non-Paid feed group pass. Ranking/diversity implementation unchanged. |
| ALPHA-BE-003 | medium | Intentional/deferred performance concern | formally deferred after Task 4 query/code review; no practical alpha failure demonstrated | [FollowRepository.get_following_ids](../backend/repositories/social_repository.py) performs one owner-scoped following-ID query; [Follow](../backend/app/models/social.py) has a follower-ID index and unique follower/following pair. List and safety-query inputs scale with follow count. Following candidate retrieval uses indexed UUID ordering with SQL LIMIT, at most ten 100-candidate batches per request, and resumable continuation; this bounds candidate scanning, not every ORM/serialization query. No correctness failure or load-related outage was reproduced; no query redesign or follow cap added. Before a high-follow-count rollout, measure query plans, latency, and memory on an isolated representative dataset. |
| ALPHA-BE-009 | medium | Dependency-blocked integration verification (historical); test skip misclassification | resolved / verified (Week 8 Task 1) | Both original Redis selectors passed against real local Redis-compatible Memurai on port 6379, with no warning filters/skips. The initial rerun still skipped despite successful PING: broad test setup handling mislabeled a disconnect deprecation warning as unavailable Redis (ALPHA-BE-011). Narrowed skips to connection/timeout failures, made authentication failures explicit, and added unique keys with targeted cleanup. TTL, expired-token absence, and real logout-to-GraphQL revocation now pass. |
| ALPHA-BE-010 | blocker | Environment prerequisite / dependency-blocked integration verification | code-complete; live smoke pending approved configuration | [MediaStorage](../backend/services/media_storage.py) retains the existing private S3/S3-compatible boundary and now rejects missing configuration, uses configured upload limits, cleans failed partial uploads, and shares retry-safe storage cleanup across media/post/account deletion. The focused media and rate-limit selectors pass (**23 passed**). No approved bucket, region, credentials, or endpoint are available in this environment, so no live object was written. Set `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY`; set `AWS_ENDPOINT_URL` only for an approved S3-compatible service. Verify upload, presigned retrieval, deletion, and failed-persistence cleanup with a disposable nonproduction object before closing the blocker. |
| ALPHA-BE-011 | medium | Confirmed dependency compatibility defect | resolved (Week 8 Task 1) | [RedisService.disconnect](../backend/services/redis_service.py) used deprecated redis-py `close()`. A real PING succeeded, then disconnect raised `DeprecationWarning` under warnings-as-errors (deprecated since redis-py 5.0.1). Replaced only that call with supported `aclose()`; added `test_redis_disconnect_uses_supported_async_close`. No token/auth semantics changed; the original skipped tests now pass against real Redis. |
| ALPHA-BE-012 | high | Application security bug | resolved (Week 8 Task 2); deterministic signed-token reproduction | [REST authentication dependency](../backend/features/auth/middleware.py) accepted refresh tokens on protected routes and skipped access-token blacklist validation for them. A real signed refresh token returned an authenticated user before the fix (`1 failed, 2 passed` in the initial boundary tests). Protected REST authentication now requires access tokens, matching GraphQL. [Token boundary tests](../backend/tests/test_auth_token_boundaries.py) verify rejection before user lookup, protected media HTTP rejection, accepted access tokens, expiration/revocation failures, inactive accounts, and the unchanged dedicated refresh flow. No Redis/session lifetime or refresh/logout policy redesign. |
| ALPHA-BE-013 | medium | Environment prerequisite | app-level query-credential acceptance fixed; external log handling remains unverified | REST registration/login credentials and refresh tokens now use JSON request bodies in the backend and active frontend; the backend rejects matching credential query keys without reflecting values. App error logging uses request paths, and ordinary Uvicorn access records are suppressed with `DEBUG=false`. This does not prove proxy/APM redaction or TLS. Before deployment, verify intermediary logging, retention, access controls, and TLS; do not send credentials in URLs. |
| ALPHA-BE-014 | high | Application startup/shutdown bug | resolved (Week 8 Task 3); deterministic mocked lifecycle reproduction | Redis client construction did not establish connectivity, yet production startup reported connected without PING. Exceptions before/at lifespan yield and stream cleanup failures could bypass dependency cleanup. [Lifespan](../backend/app/main.py) now verifies PING and uses `finally` to attempt all cleanups, reporting failures by dependency/error type without exception contents. Development dependency-failure tolerance remains unchanged. [Lifecycle regressions](../backend/tests/test_deployment_lifecycle.py) and real local production-style startup/shutdown pass. |
| ALPHA-BE-015 | high | Application readiness bug | resolved (Week 8 Task 3); deterministic lifespan + HTTP reproduction | `/health/ready` disconnected the shared RabbitMQ client acquired during startup. Readiness now owns a separate probe client and closes probe resources in `finally`. Response shape and HTTP 200/not-ready semantics are unchanged. Focused regression and real broker checks confirm the application connection remains open until shutdown. |
| ALPHA-BE-016 | blocker | Migration tooling / historical migration defect | resolved (Week 8 Task 3); reproduced in canonical CLI and disposable PostgreSQL | The local Alembic package marker shadowed installed Alembic from the backend directory; configuration rejected percent-encoded credentials and emitted a strict-warning failure for missing `path_separator`; revision 195 recreated uniqueness already defined by 001. Removed only the package marker, escaped INI interpolation, set native path separation, and made revision 195 preserve/reconcile the baseline constraint. No other historical migration was rewritten. CLI/encoded-URL/fresh-upgrade tests pass. |
| ALPHA-BE-017 | blocker | Deployment image layout defect | corrected (Week 8 Task 3); static regression verified, image build pending | [Root Dockerfile](../Dockerfile) launched `app.main` from `/app` although the package was copied to `/app/backend`, and bundled the unrelated root migration configuration. It now runs from `/app/backend` with canonical migrations and debug disabled. Added [.dockerignore](../.dockerignore) to exclude local environment secrets, venv, and caches. Build/run validation remains ALPHA-BE-019, not claimed as passed. |
| ALPHA-BE-018 | high | Missing application schema / deployment bug | resolved (Week 8 Task 3); real PostgreSQL UndefinedTable reproduction | Fresh upgrade to 212 omitted `playlists`, but the existing profile-detail resolver always queries it. [Migration 213](../backend/alembic/versions/213_profile_playlists_readiness.py) creates only the existing model's missing table/index/FK or validates required columns on a pre-existing table. No playlist feature/API was added. Real migration tests verify profile SQL reads, pre-existing playlist data preservation, and rollback/re-upgrade. |
| ALPHA-BE-019 | blocker | Environment prerequisite / dependency-blocked deployment verification | open; unassigned | Docker CLI/runtime is unavailable locally. The corrected image has not been built/launched; verify dependency installation, imports, canonical Alembic CLI, health/readiness, secret exclusion, nonproduction dependencies, and clean shutdown on the intended beta image/host before releasing it. No container tools were installed or shared services changed. |
| ALPHA-BE-020 | low | Model-only schema coverage gap / deferred behavior | deferred; no canonical runtime failure demonstrated | Fresh schema comparison found `sounds` and `search_queries` models without canonical migrations. Their repositories are not called by the current canonical API; legacy application callers are outside this beta target. Unlike the active profile playlist failure, this does not justify speculative storage/features. Reassess only if those paths are enabled; no ranking/search feature work here. |

Minimal reproduction selectors (run from the repository root, using the
project venv; no live server required):

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_collaboration_permissions.py::test_status_transitions_set_lifecycle_timestamps -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_following_feed.py::test_for_you_diversity_caps_consecutive_posts_per_creator -o addopts= -q
```

Task 4 resolved ALPHA-BE-001 without warning filters; the historical filtered
run below is preserved only as before-fix evidence. ALPHA-BE-002 now exercises
both deterministic UUID orders instead of relying on repeated random passes.
If either regression recurs, reopen its entry and preserve failing evidence.

### Completed Week 7 regression items

"Resolved" here means fixed and focused-tested in the current worktree, not
necessarily committed, deployed, or verified against a production database.

| ID | Severity when found | Classification / status | Completed fix and regression evidence |
|----|---------------------|-------------------------|---------------------------------------|
| ALPHA-BE-004 | critical | Application bug / resolved (Task 1) | GraphQL token authentication requires an active account. [Auth security tests](../backend/tests/test_auth_security.py): `test_graphql_context_rejects_non_active_accounts` covers suspended, banned, and pending-verification users. |
| ALPHA-BE-005 | high | Application bug / resolved (Task 1) | Following scans past hidden raw batches instead of falsely ending. [Feed tests](../backend/tests/test_following_feed.py): `test_following_feed_scans_past_hidden_batch_without_ending_early`. |
| ALPHA-BE-006 | high | Application bug / resolved (Task 2) | Following scanning is capped at 1,000 candidates in batches of 100 with a resumable cursor. `test_following_feed_bounds_hidden_candidate_scanning_and_continues` verifies continuation to older visible content; [frontend continuation tests](../src/app/feed-pagination.test.ts) cover empty non-terminal pages. |
| ALPHA-BE-007 | high | Application bug / resolved (Task 2) | [Feed repository](../backend/repositories/content_repository.py) orders by the same UUID key used by its cursor; `test_feed_repository_orders_by_its_uuid_keyset_cursor` checks the generated query. This is query-shape evidence, not a PostgreSQL load benchmark. |
| ALPHA-BE-008 | high | Application bug / resolved (Task 2) | New `fy2` snapshot cursors identify For You/Organic/Viral/Community. `test_ranked_feed_cursor_cannot_be_reused_with_another_algorithm` and `test_following_feed_rejects_ranked_snapshot_cursor` cover algorithm switching and Following rejection. |

Pagination compatibility boundaries intentionally retained:

- Ranked candidate pools remain bounded (For You: at most 350 combined pool
  candidates before deduplication). A null cursor ends the current eligible
  ranked snapshot, not all content ever stored in the database.
- Plain UUID cursors and validated legacy `fy1` cursors resume by finding the
  anchor in the current ranking; legacy cursors do not preserve snapshot order
  across ranking drift. New `fy2` cursors retain scoped snapshot continuation.
- A scan-capped Following page can be empty with a non-null cursor. Only a null
  cursor means terminal; clients must continue using the returned opaque cursor.

### Focused alpha backend checklist

Run only the row relevant to the change or the explicitly scheduled alpha
check. The list is **not** an instruction to run the entire backend suite.
Checked boxes record Task 4's focused automated validation, not live deployment
certification. Unchecked boxes indicate a remaining integration check or a
historical check not rerun; they are not necessarily application failures.
Use the actual interpreter and record prerequisites before running.

| Check | Acceptance criteria | Existing focused tests / verification |
|-------|---------------------|----------------------------------------|
| [x] Authentication/authorization (focused + live Redis) | Active authenticated access works; expired/revoked/inactive tokens fail; revocation-store failures fail closed; unauthenticated and cross-owner actions are rejected. | Week 7 [JWT](../backend/tests/test_auth_jwt.py)/[direct-read visibility](../backend/tests/test_direct_read_visibility.py) evidence retained; Week 8 [auth security](../backend/tests/test_auth_security.py) has 11 passing tests, including real Redis blacklist/TTL/expired-token and logout-to-GraphQL revocation. No live database user/session written. |
| [x] Feed algorithms (automated) | Default For You and explicit Organic/Viral/Community dispatch independently; safety filters apply; existing ranking behavior remains unchanged. Do not exercise or alter Paid in this checklist. | [Feed tests](../backend/tests/test_following_feed.py) and [ranking contract tests](../backend/tests/test_feed_ranking.py), run with `-k 'not paid'`. |
| [x] Pagination/cursors (automated) | Large pages continue without duplicates; snapshot drift/hidden anchors do not replay served items; malformed/cross-algorithm cursors fail; Following scan and query bounds remain effective. | Same feed file, selectors `pagination or cursor or hidden_candidate or keyset_cursor`; includes the 110-item For You page regression. |
| [x] Empty/end-of-feed (automated) | No eligible posts returns an empty result with appropriate terminal state; hidden/ineligible candidates do not falsely terminate; scan-capped empty pages retain continuation; final visible page ends without duplicates. | Added ten cases for empty/all-hidden pools across For You, Organic, Viral, Community, and Following; existing continuation/final-page tests plus [client empty-page tests](../src/app/feed-pagination.test.ts) passed. Not a live browser test. |
| [x] Onboarding/categories (automated) | Accepted values persist/read/replace for the authenticated owner; duplicates/unknown/empty selections follow existing validation; isolation and rollback remain intact. | [Preferences](../backend/tests/test_onboarding_preferences.py), [categories](../backend/tests/test_onboarding_categories.py); existing behavior/code unchanged. |
| [ ] Category deletion/privacy | User/profile/category deletion leaves no orphan relationships; soft-deleted users/profiles cannot supply recommendation categories. | [Disposable PostgreSQL deletion test](../backend/tests/test_onboarding_deletion_postgres.py). Requires `ONBOARDING_TEST_DATABASE_URL` pointing to an isolated PostgreSQL database; skip is not verification. No configured-database upgrade. |
| [x] Creator discovery (automated) | Authenticated viewer categories stay owner-scoped; eligibility, score ordering, cursors, and batched lookups preserve existing behavior. | [Discovery/handoff tests](../backend/tests/test_collaboration_handoff.py), selector `discover_creators` only; 27 passed, 8 deselected; no scoring redesign. |
| [x] Collaboration lifecycle (automated) | Initiator/participant/outsider permissions, pending acceptance, timestamps, supported transitions, and failure rollback remain intact. | [Permissions](../backend/tests/test_collaboration_permissions.py), [responses](../backend/tests/test_collaboration_response.py), [transactions](../backend/tests/test_collaboration_transactions.py): 80 passed without warning filters. |
| [x] Implemented notifications (automated) | Unread/read actions use the current user; another user's notification cannot be marked read. Do not assume unimplemented delivery transports work. | [Social interaction tests](../backend/tests/test_social_interactions.py), selector `notification`: 3 passed, 25 deselected; no live delivery claim. |
| [ ] Implemented media (automated verified; live storage blocked) | Upload validation/ownership, persistence-failure object cleanup, private access, presigned redirect, and delete ownership work. Mocked storage tests do not certify live storage credentials. | [Media upload tests](../backend/tests/test_media_upload.py): 8 passed again in Week 8. Live storage verification blocked by ALPHA-BE-010; no remote requests/object writes attempted with placeholder credentials. |
| [x] Health/error evidence (automated + local live dependencies) | Liveness and dependency readiness are distinguished; failures have standardized/sanitized error payloads; handled errors and health responses carry request IDs. | [Health/error tests](../backend/tests/test_alpha_health_errors.py): 9 passed, including all configured dependencies available and each unavailable outcome using existing mocks. Week 8 live API and fresh current-code ASGI smoke checks returned healthy/alive/ready with database, Redis, RabbitMQ all `ok`. No service was stopped to simulate failure. |

Focused command examples, from the repository root:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_auth_security.py backend\tests\test_auth_jwt.py backend\tests\test_direct_read_visibility.py -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_following_feed.py backend\tests\test_feed_ranking.py -k 'not paid' -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_onboarding_preferences.py backend\tests\test_onboarding_categories.py -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_collaboration_handoff.py -k discover_creators -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_collaboration_permissions.py backend\tests\test_collaboration_response.py backend\tests\test_collaboration_transactions.py -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_social_interactions.py -k notification -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_media_upload.py -o addopts= -q
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_alpha_health_errors.py -o addopts= -q
```

### Evidence baseline and alpha gate

Historical focused results from 2026-10-07, using Python 3.14 in the project
venv (mock-backed unless noted):

| Work | Result | Qualification |
|------|--------|---------------|
| Week 7 Task 1 new auth/feed regressions | 4 passed | Three account statuses plus hidden-batch continuation. |
| Week 7 Task 1 onboarding tests | 17 passed | Preferences/categories; not a live PostgreSQL run. |
| Week 7 Task 1 collaboration files | 80 passed with the diagnostic warning filter | Default run still exposed ALPHA-BE-001; not a clean unfiltered pass. |
| Week 7 Task 1 feed file | 75 passed, 1 failed; diversity selector then passed alone | Retained intermittent failure evidence for ALPHA-BE-002. |
| Week 7 Task 2 final feed file | 82 passed | Focused feed module only; ALPHA-BE-002 remains open despite this pass. |
| Week 7 Task 2 frontend continuation | 4 passed | Focused helper tests, not a browser/hook integration test. |
| Week 6 PostgreSQL category/deletion work | Previously verified in disposable PostgreSQL | Not rerun by Tasks 2/3; the deletion test creates ORM tables in its own schema, not an Alembic migration verification. |
| Week 7 Task 3 QA foundation | Documentation only; no tests rerun | No new executable coverage or runtime behavior changed. |

#### Week 7 Task 4 final focused results

Run on 2026-10-07 with the project Python 3.14.0 venv, base revision
`e1c9679` plus the existing Week 6/7 worktree changes. All runs used
`-c backend\pyproject.toml -o addopts= -q`, retaining warnings-as-errors;
**no warning suppression** was used. No full suite, application changes,
configured-database upgrade, or PostgreSQL run was needed for fixture
corrections and mock-backed contract checks.

| Focused selection | Result | Evidence scope |
|-------------------|--------|----------------|
| Auth security + JWT + direct-read visibility + onboarding preferences/categories | 127 passed, 2 skipped | Two Redis blacklist integration checks skipped; all other tests passed. |
| Feed + feed-ranking files, `-k 'not paid'` | 113 passed, 3 deselected | Includes both corrected UUID orders, ten empty/all-hidden cases, existing cursor/algorithm/end tests, and unchanged ranking contracts. |
| Discovery/handoff file, `-k discover_creators` | 27 passed, 8 deselected | Discovery only; marketplace/invitation changes not exercised or made. |
| Collaboration permissions + response + transactions | 80 passed | Clean unfiltered lifecycle/authorization/rollback checks. |
| Health/error tests | 9 passed | App factory/ASGI contracts; dependency connections mocked. |
| Social interactions, `-k notification` | 3 passed, 25 deselected | Existing notification ownership/read behavior. |
| Media upload tests | 8 passed | Existing validation/ownership/cleanup with mocked storage. |
| Isolated corrected lifecycle + diversity variants | 3 passed | One lifecycle test and two deterministic diversity cases; duplicated in groups above, not added to totals. |
| Existing frontend pagination helper | 4 passed | `node --experimental-strip-types --test src/app/feed-pagination.test.ts`; no broader frontend suite. |

The final backend selections above contain **367 passed, 2 skipped, and
36 deselected**, excluding the duplicated isolated checks. Before correction,
the lifecycle selector failed on three unawaited mock calls; the original
diversity selector passed alone but its non-Paid group failed (78 passed,
1 failed). Controlling the alternate post UUID made the latter deterministic
(ID 1 passed; ID 10 failed); both pass after supplying authoritative counts.
The tests now enforce the established contract rather than changing it.

No unresolved **confirmed blocker/critical/high application bug** is recorded
in this register. Week 7's scoped implementation and runnable focused stability
checks are complete. Week 6 PostgreSQL cascade evidence remains historical,
not a Task 4 rerun. The large-following-list concern is formally deferred, with
no confirmed alpha correctness blocker or representative production-scale
benchmark claimed.

#### Week 8 Task 1: live dependency sign-off evidence

Verified on 2026-10-07 with the project Python 3.14.0 venv, base revision
`e1c9679` plus the existing worktree changes. No full suite, configured-database
migration, Redis flush, bucket creation, shared-service shutdown, or product
feature change was performed.

| Item | Result / sign-off status | Prerequisite or qualification |
|------|--------------------------|-------------------------------|
| Redis authentication/blacklist | **Verified locally** | Real local Memurai Redis-compatible service answered PING. Unique test token keys were deleted in `finally`; no unrelated keys touched. Blacklist TTL, expired-token absence, and active-before/logout/rejected-after GraphQL access passed. User lookup was mocked; token generation, logout, Redis storage, and GraphQL revocation checks were real. |
| Media/storage | **Alpha sign-off blocker: environment prerequisite** | No explicit storage endpoint; configured credentials are placeholders. Mocked upload/access/persistence-failure cleanup tests passed; no live object round trip attempted. Provide an approved test bucket/endpoint and permissions for upload/read/presign/delete. Use uniquely generated object keys and delete only test-created objects. Do not introduce another provider or alter the configured database to satisfy verification. |
| Health/readiness, live dependencies | **Verified locally** | Existing API at `127.0.0.1:8002` returned HTTP 200: `/health` healthy, `/health/live` alive, `/health/ready` ready with database/Redis/RabbitMQ `ok`. All echoed the supplied `X-Request-ID`. A fresh app factory from current code with real dependencies returned the same results via ASGI transport, without lifespan startup or migration operations. |
| Health/readiness, unavailable dependencies | **Verified by existing test infrastructure** | Existing parameterized health tests simulate database false/exception, Redis false/exception, and RabbitMQ exception and assert `not_ready` with the exact failing dependency. No shared live service was stopped. HTTP 200 with `not_ready` remains intentional existing behavior. |
| Large Following list | **Deferred scale concern** | ALPHA-BE-003 unchanged; no new ranking/feed/query work. |

Exact focused shell test command (from repository root):

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_auth_security.py backend\tests\test_media_upload.py backend\tests\test_alpha_health_errors.py -o addopts= -q -rs
```

- Initial run: **24 passed, 2 skipped**. Direct Redis PING succeeded;
  warnings-as-errors then reproduced the deprecated disconnect call. This was
  a concrete compatibility failure masked by broad test skip handling, not
  proof that Redis was unavailable.
- After `aclose()` and the focused close regression: **27 passed, 0 skipped**.
- Final run including real logout-to-GraphQL revocation: **28 passed, 0 skipped,
  0 failed** (auth security 11, media contracts 8, health/errors 9). Warnings
  remained errors; no filters were added.

Exact live HTTP smoke command:

```powershell
foreach ($path in @('/health', '/health/live', '/health/ready')) {
  $response = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:8002' + $path) -Headers @{'X-Request-ID'='alpha-week8-dependencies'} -TimeoutSec 20
  Write-Output "$path HTTP $($response.StatusCode) request-id=$($response.Headers['X-Request-ID'])"
  Write-Output $response.Content
}
```

Additional focused runtime probes used the selected interpreter and
`backend` working directory: sanitized configuration endpoint checks; real
Redis PING/disconnect with warnings-as-errors; and `httpx.ASGITransport` against
a fresh `create_app()` with real dependency checks. The editor snippet process
inherited `DEBUG=release`, which is not a valid boolean; these probes explicitly
set process-local `DEBUG=false`. No configuration file or running service was
changed. The shell pytest runs required no such override.

Changed-file diagnostics and Python syntax checks passed. Documentation links
and `git diff --check` were validated. The minimal runtime change was
`RedisService.disconnect: close() -> aclose()`; no auth policy, media, health,
Paid, marketplace/payment, ranking, or completed Week 6 behavior changed.

**Week 8 Task 1 is not fully signed off:** Redis and health prerequisites are
verified locally, but live storage verification remains blocked by
ALPHA-BE-010. Verification work completed as far as safely possible in this
environment; full alpha approval is withheld until that prerequisite is
satisfied. No Task 2 work started.

#### Week 8 Task 2: security and production-readiness audit

Performed on 2026-10-07 against base `e1c9679` plus the existing worktree.
The bounded read-only security review found no initial confirmed vulnerability;
subsequent signed-token regression testing identified ALPHA-BE-012. The user
approved fixing all confirmed findings. Only that runtime defect was changed.
This is scoped alpha evidence, not a penetration test or production deployment
certification.

| Area | Evidence / classification |
|------|---------------------------|
| Authentication and authorization | GraphQL requires active, non-revoked access-token identity; REST now has the same access-token boundary. Focused tests cover suspended/banned/pending-verification accounts, anonymous requests, token expiry, revocation failure, and ownership on direct reads/media/onboarding. Active but email-unverified signup remains intentional existing behavior. |
| Inputs and abuse controls | Existing tests verify canonical preference/category values, user isolation, invalid IDs/inputs, GraphQL aliases/fragments/batches, endpoint/user/IP buckets, failed-attempt quota consumption, and configured limiter wiring. No new quotas or schema redesign. Limits remain process-local; multi-worker/multi-instance deployment needs an explicit abuse-control assessment, not a claim of globally shared quotas. |
| Data/query boundaries | Owner-scoped onboarding/profile/category paths and deleted-user/profile category filters retain Week 6 behavior. Existing repository queries use SQLAlchemy expressions/bound values in the reviewed paths; no injection defect was identified. No ranking or database architecture changes; no database migration or live query benchmark. |
| Sensitive responses and errors | Direct-read visibility, private media ownership, structured error envelopes, handled-response request IDs, unexpected REST error sanitization, and streaming response exclusion of sources/credentials passed. No newly exposed internal fields. REST auth values are body-only; downstream proxy/APM body-log configuration is not verified (ALPHA-BE-013). |
| Production configuration | Existing rejection of production debug/default/short JWT secrets tested; added actual app-factory checks for disabled REST docs, security headers/CSP, and allowlisted CORS preflight acceptance/rejection. No live configuration changed. Deployment must explicitly set production mode, supply secure secrets and approved origins, terminate TLS, restrict log access, and verify dependency credentials/network boundaries. Tests are not evidence that the current local development configuration is production-safe. |
| Dependencies and deferred items | Real Redis verification remains Week 8 Task 1 evidence; the three live selectors were deliberately deselected, not rerun or counted as passes. Live storage ALPHA-BE-010 remains a blocker/environment prerequisite. Large-following-list ALPHA-BE-003 remains a deferred scale concern. |

Files changed for Task 2:

- [REST auth middleware](../backend/features/auth/middleware.py): require
  `ACCESS_TOKEN_TYPE` instead of accepting access or refresh tokens.
- [Token boundary tests](../backend/tests/test_auth_token_boundaries.py):
  ten focused cases, including real signed JWTs and a protected media request.
- [Production config tests](../backend/tests/test_production_config.py):
  four additional cases for short secrets, docs/headers, and both CORS outcomes.
- This guide: resolved security finding, transport/deployment caveats, evidence.

Exact commands from the repository root:

```powershell
# Initial focused baseline: 206 passed.
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_production_config.py backend\tests\test_auth_rate_limits.py backend\tests\test_rate_limits.py backend\tests\test_media_rate_limits.py backend\tests\test_streaming_rate_limits.py backend\tests\test_auth_jwt.py backend\tests\test_direct_read_visibility.py backend\tests\test_onboarding_preferences.py backend\tests\test_onboarding_categories.py backend\tests\test_alpha_health_errors.py -o addopts= -q -rs

# Before-fix reproduction: 1 failed, 2 passed.
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_auth_token_boundaries.py -o addopts= -q

# Final focused security/config/ownership validation: 238 passed, 3 deselected.
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_auth_token_boundaries.py backend\tests\test_production_config.py backend\tests\test_auth_rate_limits.py backend\tests\test_rate_limits.py backend\tests\test_media_rate_limits.py backend\tests\test_streaming_rate_limits.py backend\tests\test_auth_jwt.py backend\tests\test_auth_registration.py backend\tests\test_direct_read_visibility.py backend\tests\test_onboarding_preferences.py backend\tests\test_onboarding_categories.py backend\tests\test_alpha_health_errors.py backend\tests\test_media_upload.py backend\tests\test_auth_security.py -k 'not blacklist_token_marks_jti and not blacklist_token_ignores_expired_token and not live_redis_logout' -o addopts= -q -rs

# Sensitive streaming contract: 3 passed.
& '.\.venv\Scripts\python.exe' -m pytest -c backend\pyproject.toml backend\tests\test_streaming_api_contract.py -o addopts= -q -rs

& '.\.venv\Scripts\python.exe' -m compileall -q backend\features\auth\middleware.py backend\tests\test_auth_token_boundaries.py backend\tests\test_production_config.py
git --no-pager diff --check
```

Final nonduplicate total: **241 passed, 3 deselected, 0 failed, 0 skipped**.
Warnings remain errors; no warning filters. The initial ten-file baseline
was **206 passed** before adding boundary/config coverage. VS Code test discovery
returned no tests, so the explicit focused pytest commands above were used.
Changed-file editor diagnostics and Python syntax checks passed.
Mocks/ASGI transport were used for these checks; no lifespan startup, live
storage writes, configured database upgrades, full suites, or Redis writes.

**Week 8 Task 2 is complete for the scoped audit and confirmed fix.** No
unresolved confirmed security defect remains in the reviewed paths. Overall
alpha/production sign-off is still withheld for live storage and deployment
prerequisites, including the query-credential logging boundary above. No
Week 8 Task 3 work was started. Paid, marketplace/payment/fund-hold, ranking,
and completed Week 6 behavior were unchanged.

#### Week 8 Task 3: controlled beta deployment readiness

Audited on 2026-10-07 against base `e1c9679` plus the existing worktree.
The canonical target is **`backend/app/main.py`**, its environment-backed
`Settings`, and **`backend/alembic.ini`**. The root image now runs that target.
The canonical container path is the root [Dockerfile](../Dockerfile) and root
[docker-compose.yml](../docker-compose.yml). Use the root [.env.example](../.env.example)
for Compose inputs; it contains blank secret values, not deployable credentials.
`docker/docker-compose.yml` and `docker/Dockerfile.backend` are legacy
development scaffolding (reload/debug, default credentials, management ports,
and an obsolete build layout), not the beta deployment path. Do not reuse them
unchanged.
No deployment orchestrator or replacement dependency was introduced.

##### Environment contract

Copy the root `.env.example` to root `.env` for Compose interpolation and set
all required values. Generate URL-safe passwords because Compose constructs
PostgreSQL, Redis, and RabbitMQ URLs using those values. Apply migrations
explicitly with `docker compose run --rm api alembic upgrade head`; API startup
does not modify the schema. The [backend example](../backend/.env.example)
remains the standalone local `Settings` template. Do not commit populated
environment files; the image build context excludes `.env` and `.env.*`.
For beta, inject approved secrets through the deployment mechanism.

| Configuration | Beta requirement |
|---------------|------------------|
| `ENVIRONMENT`, `DEBUG` | Explicitly `production` and `false`. `staging` does not inherit all production checks. Production rejects debug, the default JWT key, and keys shorter than 32 characters. No reload mode. |
| `JWT_SECRET_KEY`, `JWT_ALGORITHM` | Unique high-entropy deployment secret, shared consistently by the intended API replicas; retain existing `HS256` unless a separately reviewed policy change is required. Do not rotate silently during rollback. Existing access/refresh TTL defaults remain unchanged. |
| `DATABASE_URL` | `postgresql+asyncpg://...` pointing to the approved beta database. Encode special characters in URL credentials. PostgreSQL must provide pgvector and permit its installation during fresh provisioning. Runtime role privileges and network/TLS boundaries must be configured by the operator. |
| `DATABASE_URL_SYNC` | Optional `postgresql+psycopg://...` URL for a controlled migration role; otherwise derived from `DATABASE_URL`. Never use the root SQLite migration configuration. |
| `REDIS_URL` | Approved Redis/Redis-compatible host, database, authentication, and transport security. Set the complete URL; separate host/password settings are not automatically composed into it. Redis is required for production startup/auth revocation. |
| `RABBITMQ_URL` | Approved broker/user/vhost and transport security; do not rely on development guest credentials. Set the complete URL. Production requires broker connection success. |
| `CORS_ORIGINS` | JSON list of exact HTTPS frontend origins, e.g. `["https://beta.example.com"]`, **not** comma-separated text. Keep credentials enabled only with the approved origins; tests cover accepted and rejected preflight. |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` for the controlled beta. Restrict sink access/retention. Non-debug logging suppresses ordinary Uvicorn access records and noisy SQL/boto logs; verify proxy/APM handling of sensitive request data. |
| `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Required for the existing private S3/S3-compatible storage adapter; supply approved beta values through secrets. `AWS_ENDPOINT_URL` is optional and must be set only for an approved S3-compatible endpoint; leave it empty for approved AWS S3. No live values are present in the example or source. |
| `MEDIA_MAX_IMAGE_BYTES`, `MEDIA_MAX_VIDEO_BYTES` | Optional positive byte limits; defaults are 8,388,608 (8 MiB) and 536,870,912 (512 MiB). |
| Root Compose inputs | `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `JWT_SECRET_KEY`, and `CORS_ORIGINS` are required without credential defaults. Root Compose uses `pgvector/pgvector:pg16` and service URLs at `postgres`, `redis`, and `rabbitmq`. |
| `FFMPEG_PATH`, `STREAMING_*_DESTINATION_URL` | Existing streaming needs FFmpeg and approved per-platform RTMP/RTMPS destinations. Local executable found, but no stream started. The root image does not install FFmpeg; provision/validate it on the beta target before enabling that existing path. Never use the local test destinations as beta endpoints. |
| Other settings | Review existing rate-limit, pool, lease, cache, and pagination defaults for the selected single-process beta footprint; no policy changes here. OpenAI/Anthropic keys are not required by the verified API startup/readiness path; configure only if an existing enabled integration actually needs them. |

TLS termination, DNS, frontend origin, access-log redaction, secret rotation,
backups, dependency firewall/TLS settings, storage permissions, and replica
topology **cannot be certified from this local environment**. Rate limits and
stream-process ownership are process-local; the current default single-worker
image is the validated code footprint, not proof of multi-replica readiness.
Legacy `SESSION_SECRET`, `ALLOWED_HOSTS`, and `REQUIRE_HTTPS` variables do not
configure the canonical backend's settings and must not create a false sense
of TLS/host enforcement.

##### Migration readiness and rollback

- The canonical graph has **one base (`001`), one head (`217`), 43 revisions**,
  with existing merge points intact. The deployment tail is:
  **206 -> 207 -> 208 -> 209 -> 210 -> 211 -> 212 -> 213 -> 214 -> 215 -> 216 -> 217**.
- Revisions 207-210 remain required schema dependencies despite disabled Paid
  delivery. They were executed only as part of isolated migration validation;
  their functionality, formulas, flags, and migration code were unchanged.
- 211 stores onboarding collaboration types; 212 provides seeded canonical
  categories and cascade-protected associations; 213 supplies the existing
  profile resolver's playlist table. No completed Week 6 feature was changed.
- PostgreSQL **18.6 with pgvector available** was locally verified. Four
  uniquely named disposable databases exercised fresh/baseline variants:
  upgrade to head, category seeds/cascades, playlist SELECT, downgrade to 206,
  and re-upgrade to head. Cases cover both existing/missing historical
  uniqueness and pre-existing/new playlist tables, preserving existing
  playlist rows. All databases were dropped in fixture `finally` cleanup.
  A separate disposable model-column audit database was also dropped.
- No configured database was upgraded, stamped, downgraded, or repaired.
  Existing beta data needs a backup/restore drill and its **own** current
  revision/schema assessment before migration. Do not stamp over drift.
- The exceptional historical edit is limited to revision 195's duplicated
  baseline constraint. Downgrade keeps uniqueness required by 001. Revision
  213 downgrade likewise deliberately retains playlist data because the table
  may predate reconciliation. **Downgrade is not an exact schema restoration.**
  211/212 and earlier tail downgrades can destroy data. The empty/fixture
  rollback exercise is not approval to downgrade a populated beta database.

Controlled deployment checklist (operator actions, **not executed here**):

1. Freeze a reviewed release artifact/image and matching canonical migration
   files; retain the previous known-good artifact. Ensure the uncommitted
   fixes/migrations in this worktree are included before building a release.
2. Provision approved beta dependencies and secret injection; explicitly check
   production/debug/origins without dumping values. Restrict dependency and
   management ports; resolve ALPHA-BE-010, ALPHA-BE-013, ALPHA-BE-019, and any
   streaming prerequisites for the enabled beta footprint.
3. Snapshot the beta database and demonstrate restoration in a disposable
   environment. Capture its revision with the canonical configuration.
4. Run migration validation against that restored copy, then apply migrations
   once with the authorized migration role during an approved rollout window.
   API startup does not create/stamp/upgrade the schema automatically.
5. Launch the canonical API without reload. For a native environment from the
   repository root, the existing entry point is
   `uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000`.
   Configure proxy trust/TLS at the actual deployment boundary; no local
   production server or shared process was started/stopped by this task.
6. Check `/health` healthy and `/health/live` alive, then require
   `/health/ready` HTTP 200, JSON `status == "ready"`, and all three dependency
   checks `ok`; dependency failures return HTTP 503. Check `X-Request-ID`,
   structured errors, CORS, and security headers through the deployed proxy.
   Readiness does not check migration revision, media storage, or FFmpeg;
   verify those independently.
7. Test ordinary graceful shutdown/restart, observe cleanup diagnostics, and
   confirm no orphaned application process/connection. Do not kill shared
   services by process name.
8. On failure, stop/drain new traffic and roll back the application artifact
   only after confirming schema compatibility. Prefer restoring the verified
   snapshot if database rollback is necessary; do not blindly run destructive
   Alembic downgrades. Preserve diagnostic request IDs without credential URLs.

Canonical migration commands for an **approved target only**, from repository
root using its intended injected environment:

```powershell
& '.\.venv\Scripts\python.exe' -m alembic -c backend\alembic.ini heads
& '.\.venv\Scripts\python.exe' -m alembic -c backend\alembic.ini current
# Operator-controlled migration, NOT run against the configured database here:
# & '.\.venv\Scripts\python.exe' -m alembic -c backend\alembic.ini upgrade head
```

##### Validation evidence and exact commands

Final focused command below: **33 passed, 0 failed, 0 skipped**:
7 migration cases, 11 lifecycle/image/logging cases, 6 production configuration
cases, and 9 health/error cases. `BETA_TEST_DATABASE_URL` is opt-in permission
to create/drop uniquely named databases on that server; tests never upgrade
its named source database. The local run below derived that value privately
without printing it.

```powershell
@'
import os
import pytest
from app.config import settings
os.environ['BETA_TEST_DATABASE_URL'] = settings.sync_database_url
raise SystemExit(pytest.main(['-c', r'backend\pyproject.toml', r'backend\tests\test_beta_migrations_postgres.py', r'backend\tests\test_deployment_lifecycle.py', r'backend\tests\test_production_config.py', r'backend\tests\test_alpha_health_errors.py', '-o', 'addopts=', '-q', '-rs']))
'@ | & '.\.venv\Scripts\python.exe' -
```

Before-fix evidence:

- `pytest -c backend\pyproject.toml backend\tests\test_deployment_lifecycle.py
  -o addopts= -q`: **7 failed** (missing PING, cleanup paths, image layout, and
  absent secret-exclusion file). Corrected group later passed.
- Isolated `test_readiness_does_not_disconnect_running_application_broker`:
  **1 failed**, showing the shared client was disconnected by the probe.
- `python -m alembic -c alembic.ini heads` from `backend`: failed due to the
  local package marker; the CLI regression now passes.
- Disposable migration runs successively reproduced strict path-separator
  warning, percent-URL interpolation failure, and duplicate baseline
  constraint. After fixes, the original fresh/rollback checks passed.
- Fresh head-212 profile playlist SELECT: **1 failed, 4 deselected** with
  PostgreSQL `UndefinedTable`; migration 213 resolves it.
- `docker version --format '{{.Server.Version}}'`: executable unavailable.
  No image build/run result is claimed.

A fresh `create_app()` with actual lifespan, process-only production mode,
debug false, a generated unprinted JWT key, and JSON allowlisted origin
completed startup/shutdown against real local dependencies. ASGI requests
returned **HTTP 200**, exact request ID, healthy/alive/ready; readiness reported
database/Redis/RabbitMQ all `ok`. The application broker connection remained
open after readiness and closed at shutdown. No migration, queue message,
cloud write, token blacklist write, or active stream was created in this smoke.
These are local dependency results, not remote beta-network certification.

Structured JSON log output and production access-log suppression were tested;
handled errors/request IDs retained their existing contract. Startup/cleanup
failures now log error **types**, not raw exception contents. Proxy/APM sinks
and third-party connection-failure diagnostics still need deployment review;
no blanket guarantee of secret-free external logs is asserted.

Syntax/changed-file diagnostics, local documentation links, and
`git diff --check` passed. No full backend/frontend suite was run. Missing
Alembic tooling was installed in the project venv only after the import failed,
using the existing declared `alembic>=1.14.0,<2.0` dependency; no manifest change.
Final validation commands also included:

```powershell
& '.\.venv\Scripts\python.exe' -m compileall -q backend\app\main.py backend\alembic\env.py backend\alembic\versions\195aea895888_add_collaboration_participant_.py backend\alembic\versions\213_profile_playlists_readiness.py backend\tests\test_deployment_lifecycle.py backend\tests\test_beta_migrations_postgres.py backend\tests\test_alpha_health_errors.py
git --no-pager diff --check
```

The final local link check validated **42 targets**. A read-only
`pg_database` query confirmed **zero remaining task disposable databases**.

**Task 3 preparation and local validation are complete, but beta deployment
readiness is not fully signed off:** live storage, actual image build/run, and the
operator-specific environment/rollback prerequisites above remain open.
At Task 3 close on 2026-10-07, Week 8 Task 4 had not started.

#### Week 8 Task 4: beta user-flow validation

Focused validation was performed on 2026-10-07 in the Python 3.14.0 project
venv, against `e1c9679` plus the existing worktree changes. This was not a
browser-driven end-to-end run: backend checks use existing resolver/service
contracts and doubles; frontend checks use the existing Node test files. No
full backend/frontend suite, configured-database mutation, migration, or live
storage write was performed.

| Flow | Result | Defect / severity / reproduction | Fix or test evidence | Remaining prerequisite |
|------|--------|-----------------------------------|---------------------|------------------------|
| Registration, login, logout, expiry/revocation, active/suspended/banned accounts | **PASS** for focused contracts | REST auth query-credential acceptance was fixed; severity N/A after remediation. | Backend auth selectors: **25 passed** in the focused query-credential run. Frontend auth-store selectors: **9 passed**. Existing Week 8 Task 1 real Redis blacklist/TTL/logout-revocation result remains valid; live Redis integration was not repeated here. | Beta deployment still requires the approved Redis/auth configuration and secure deployment boundary; ALPHA-BE-013 tracks unverified intermediary body logging/TLS controls. |
| Onboarding completion, collaboration preferences, response time, open-to-collaboration, canonical categories, persistence/readback | **PASS** for backend/client contracts; not browser E2E | No defect reproduced; severity N/A. | Preferences/categories: **17 passed**. Frontend auth-store includes authenticated preference/category save and readback. | None for mocked/local contract checks. |
| Account deletion and privacy | **PARTIAL; database cascade verification not run** | No defect reproduced; severity N/A. | The onboarding/category contracts cover owner scoping and deleted-profile category exclusion. The disposable PostgreSQL deletion test was not run. | `ONBOARDING_TEST_DATABASE_URL` was absent. Provide an isolated PostgreSQL target before running deletion/cascade verification; no configured database was changed. |
| Following, For You, Organic, Viral, Community; switching, cursors/pagination, empty/end states, hidden/deleted content | **PASS** for selected backend/client contracts | No defect reproduced; severity N/A. | Selected feed/ranking tests: **44 passed, 72 deselected**; invalid direct feed arguments: **3 passed, 90 deselected**. Frontend empty-page/terminal pagination tests are included below. | No live/browser feed run or production-scale ranking/load claim. Paid and ranking redesign were excluded. |
| Creator discovery, category compatibility, viewer ownership/isolation, discovery-to-collaboration handoff | **PASS** for automated contracts | No defect reproduced; severity N/A. | Discovery/scoring/search selection: **27 passed, 38 deselected**. Collaboration creation/handoff selection: **13 passed, 27 deselected**, including discovery-to-pending-participant handoff. | No live database discovery run. |
| Collaboration create/accept/decline, lifecycle, permissions, existing completion behavior, messaging handoff | **PASS** for focused contracts | No defect reproduced; severity N/A. | Request/response/messaging/lifecycle selection: **17 passed**. Broader permissions/response/transaction evidence of **80 passed** from the existing register was retained, not rerun. | No live messaging transport verification. |
| Notification creation and read/unread ownership | **PASS** for selected in-app notification behavior | No application defect reproduced; severity N/A. The existing like-flow test lacked a positive creation assertion (coverage gap, not a reproduced bug). | Strengthened the existing like-flow test to assert one `NEW_LIKE` notification with the owner, actor, title, body, and post ID. Creation/read/ownership selection: **4 passed, 24 deselected**. | No external delivery transport or persistent live notification delivery was exercised. |
| Media upload/access/delete/cleanup contracts | **PASS** with mocked storage only | No application defect reproduced; severity N/A. | Media plus health/error selection: **29 passed, 1 deselected**; existing media tests assert validation, ownership, presigned access contract, and cleanup after persistence failure. | **ALPHA-BE-010 remains a blocker/environment prerequisite** for real storage verification: approved non-production endpoint/bucket and scoped credentials. No cloud writes were attempted. |
| Authentication/authorization errors, invalid inputs, empty feeds, unavailable dependencies, structured errors | **PASS** for automated contracts | No defect reproduced; severity N/A. | Included in the auth, feed, onboarding, media/health groups above; health/error checks simulate dependency failures and validate structured responses. | Live dependency failure injection was not performed against shared services. |
| Frontend auth/onboarding and feed pagination contracts | **PASS**; not browser E2E | No defect reproduced; severity N/A. | `node --experimental-strip-types --test src/app/auth-store.test.ts src/app/feed-pagination.test.ts`: **13 passed**. | No browser automation or full frontend suite was run. |

Focused Python syntax compilation passed for the exercised backend modules and
tests. Editor diagnostics reported **no errors in the 18 selected Python and
TypeScript files** checked. The `test_social_interactions.py` change is test-only;
no application code was changed because no concrete application defect was
reproduced. At Task 4 close on 2026-10-07, Task 5 had not started.

**Week 8 Task 4 is COMPLETE for the requested focused local validation.** The
account-deletion PostgreSQL check and live media storage remain explicitly
unverified environment prerequisites; this is not a claim of production or
full beta sign-off.

#### Week 8 Task 5: final beta launch checklist and readiness decision

This final focused readiness review reconciles the recorded Week 6 completion,
Week 7 stability work, and Week 8 Tasks 1-4. It uses their focused test and
isolated-database evidence; it does not repeat those suites, start a deployment,
or modify the configured database or live storage. The review found no new
application defect. Environmental gates remain open, so completing this task
does not grant release approval.

##### Reconciled completion evidence

| Work | Status | Evidence and limits |
|------|--------|---------------------|
| Week 6 onboarding preferences, canonical categories, persistence, recommendation integration, and privacy protections | **VERIFIED in focused local and isolated PostgreSQL checks** | Week 8 Task 3 verified canonical migrations/seeds/cascades and profile playlist schema against disposable PostgreSQL; Task 4 verified preferences/categories, authenticated owner isolation, and recommendation category filtering contracts. The dedicated account/profile/category deletion privacy integration test was not run in Task 4 because `ONBOARDING_TEST_DATABASE_URL` was absent; retain that check as a pre-beta gate, not as a known defect. |
| Week 7 critical/high bug fixes, feed pagination, QA register, and alpha stability | **VERIFIED in recorded focused tests; scale evidence bounded** | ALPHA-BE-004 through ALPHA-BE-008 were resolved with regression tests. Week 7 focused auth/feed/onboarding/collaboration results and deterministic stability corrections remain documented above. Following scanning is bounded at 1,000 candidates (10 batches of 100) with continuation; this is correctness/scan-bound evidence, not a representative production-scale benchmark. ALPHA-BE-003 remains deferred. |
| Week 8 Task 1 dependency verification | **LOCALLY VERIFIED, except storage** | Redis authentication/revocation and health/readiness were verified against local dependencies. ALPHA-BE-010 live storage is still blocked; do not treat mocked media tests as a cloud round trip. |
| Week 8 Task 2 security/production-readiness audit | **SCOPED REVIEW COMPLETE** | ALPHA-BE-012 was reproduced, fixed, and regression-tested. No unresolved confirmed security bug was found in the reviewed scope. This is not a penetration test; proxy/APM handling of query-carried credentials, beta secrets, TLS, network boundaries, and deployed log access remain unverified. |
| Week 8 Task 3 deployment readiness | **CODE/MIGRATION PREPARATION VERIFIED; DEPLOYMENT NOT VERIFIED** | The canonical migration tail was exercised on disposable PostgreSQL and startup/readiness/shutdown against local dependencies. ALPHA-BE-019 Docker image build/run remains blocked because Docker tooling is unavailable; operator-specific target, backup/restore, and deployment checks are outstanding. |
| Week 8 Task 4 beta user-flow validation | **FOCUSED LOCAL VALIDATION COMPLETE** | Focused backend/frontend contracts passed as recorded above; notification creation assertion was added. Live storage and PostgreSQL deletion verification remain open. No full suite or browser E2E is claimed. |

##### Beta acceptance checklist

`VERIFIED` means supported by the listed focused evidence in this worktree; it
does not imply deployed production certification. Items marked required remain
release gates.

| Acceptance area | Status | Acceptance evidence / remaining action |
|-----------------|--------|----------------------------------------|
| Authentication | **VERIFIED** | Registration, login, logout, active/suspended/banned behavior, expiry, refresh-token boundaries, and revocation are covered. Live Redis verification remains existing Task 1 evidence. |
| Onboarding | **VERIFIED** | Authenticated preference save/readback and owner isolation; client save/readback contracts pass. |
| Categories and privacy | **VERIFIED / REQUIRED BEFORE BETA** | Canonical seed/schema, persistence, owner scoping, and recommendation category filters verified. Run account/profile/category deletion and soft-delete privacy integration against an isolated PostgreSQL target. |
| Feed algorithms | **VERIFIED** | Following, For You, Organic, Viral, Community dispatch and visibility filters pass focused tests; Paid/ranking redesign excluded. |
| Pagination and empty/end behavior | **VERIFIED** | Cursor scoping, continuation, hidden/deleted filtering, invalid input, empty and terminal pages covered. No production-scale load benchmark claimed. |
| Creator discovery | **VERIFIED** | Candidate eligibility, category ownership/compatibility, scoring/pagination contracts, and discovery-to-collaboration handoff covered. |
| Collaboration | **VERIFIED** | Create, accept/decline, lifecycle timestamps, permissions, and existing messaging handoff covered. Provider-neutral collaboration payment recovery is tracked separately below; no real money movement is implemented. |
| Notifications | **VERIFIED** | Existing in-app creation payload, ownership, and read/unread contracts verified. No external delivery guarantee. |
| Media | **BLOCKED / NOT CERTIFIED** | 23 focused media/storage tests passed. ALPHA-BE-010 requires approved storage configuration, endpoint/bucket, and scoped credentials for live upload/retrieval/deletion/cleanup verification. No live storage smoke test was attempted. |
| Error handling | **VERIFIED** | Focused checks cover auth/authorization and validation errors, sanitized unexpected failures, request IDs, empty feeds, and mocked unavailable dependencies. |
| Production configuration | **VERIFIED** | 56 focused production-config tests passed; `git diff --check` passed. With `ENVIRONMENT=production`, configuration fails closed for missing/unsafe required dependency settings. DEBUG boolean parsing, invalid-input validation, and the production prohibition are verified. This does not certify deployed secrets or endpoints. |
| Sensitive logging | **VERIFIED** | Raw exception text, traceback output, request paths, and sensitive error details were removed from the identified global exception-handler and database-connection logging paths. 16 focused logging/error tests passed. Independent server/proxy/APM/access-log handling remains unverified. |
| Docker/container (Gate 2C) | **BLOCKED** | Docker CLI unavailable; no installation attempted. Container build/start/health/migration/shutdown certification remains blocked. |
| Security | **VERIFIED / REQUIRED BEFORE BETA** | REST credential query acceptance is fixed and tested. Before release, verify proxy/APM/access-log handling, require TLS, and restrict log access. |
| Database and migrations | **VERIFIED / REQUIRED BEFORE BETA** | Canonical head/tail, fresh upgrade, rollback/re-upgrade, seed/cascade, and playlist compatibility were tested on disposable PostgreSQL. Before release, inspect the actual beta database revision/schema, snapshot it, and demonstrate restoration; never blindly downgrade populated data. |
| Dependencies | **VERIFIED LOCALLY / ENVIRONMENT PREREQUISITE** | Local database/Redis/RabbitMQ readiness was verified. Provision and verify approved beta dependency endpoints, credentials, TLS/network restrictions, and production readiness on the target. |
| Deployment configuration | **REQUIRED BEFORE BETA** | Inject production secrets; explicitly set production/debug/origins; configure TLS termination, CORS, proxy trust, dependency firewalling/ports, and single-worker or approved replica topology. No live configuration was changed. |
| Observability | **VERIFIED LOCALLY / REQUIRED BEFORE BETA** | Health/readiness, structured error envelopes, and request IDs pass locally. Validate request IDs and secret-safe logs through deployed proxy/APM; check readiness JSON/dependency checks rather than HTTP status alone. |
| Rollback readiness | **REQUIRED BEFORE BETA** | Build and retain the exact release artifact, validate schema compatibility, and perform a backup/restore drill on a disposable copy. Prefer artifact rollback unless a reviewed data restore is required; migrations are not generally lossless downgrades. |

##### Remaining prerequisites and classification

| Classification | Prerequisite | Gate / disposition |
|----------------|---------------|--------------------|
| **BLOCKER** | **ALPHA-BE-010 live storage** | No approved live test endpoint/bucket or scoped credentials were available. Keep beta launch blocked until safe non-production upload, read/presign, delete, and failed-persistence cleanup are verified. |
| **BLOCKER** | **ALPHA-BE-019 container build/run** | Gate 2C remains **BLOCKED**: Docker CLI unavailable; no Docker installation attempted. Build/start/health/migration/shutdown certification has not been performed on the intended container target. |
| **REQUIRED BEFORE BETA** | Account-deletion/category privacy integration | Run [the deletion test](../backend/tests/test_onboarding_deletion_postgres.py) with `ONBOARDING_TEST_DATABASE_URL` pointed only at an isolated PostgreSQL database. The variable was absent in Task 4; no target was created or database changed. |
| **REQUIRED BEFORE BETA** | Beta secrets, TLS, origins, dependency network/access controls | Supply approved production secrets/dependency URLs and exact approved HTTPS origins; configure TLS termination, restricted ports, trusted proxy configuration, and database/Redis/RabbitMQ credential/network security. Validate on the deployed environment without exposing values. |
| **REQUIRED BEFORE BETA** | Proxy/APM/access-log credential handling (ALPHA-BE-013) | Identified application exception logs are verified safe, but independent proxy/APM/access-log handling of credential-bearing queries, bodies, and headers, retention, access, and TLS remain unverified. Confirm intermediaries do not retain/expose sensitive data and enforce TLS before exposure. |
| **REQUIRED BEFORE BETA** | Existing beta database assessment and backup/restore | Confirm the target's actual revision/schema, take a recoverable backup, and demonstrate restore in a disposable environment before migration/release. The disposable migration graph test is not a backup/restore drill. |
| **REQUIRED BEFORE BETA** | Process-local rate limits and topology | Rate limits are process-local. Either constrain and document the beta to the validated single-worker footprint or complete an explicit multi-worker/replica abuse-control assessment before using that topology; do not assume quotas are globally shared. |
| **ENVIRONMENT PREREQUISITE** | FFmpeg and streaming destinations, if streaming is enabled | Provision/validate FFmpeg and approved RTMP/RTMPS destinations on the beta target before enabling existing streaming. If streaming is not enabled for beta, keep it disabled; this is not a new feature or an unconditional launch gate. |
| **DEFERRED / POST-BETA** | ALPHA-BE-003 large Following-list scale concern | No practical alpha correctness failure was reproduced; candidate scanning is bounded and resumable. Benchmark query plans, latency, and memory on a representative isolated high-follow-count dataset before scaling that footprint. |
| **DEFERRED / POST-BETA** | ALPHA-BE-020 unused model-only schema coverage | No current canonical API caller or runtime failure demonstrated. Reassess only if the legacy sound/search paths become part of the beta scope. |
| **VERIFIED** | Focused Week 6-8 application behavior and local dependencies | Use the evidence tables above; this status does not waive any blocker or operator prerequisite. |

##### Collaboration Funds-Hold Task 4

**COMPLETE — provider-neutral failure/recovery paths only.** Migration 217 adds
the minimum cancellation, refund-authorization, and dispute audit data.
Focused tests cover collaboration-bound cancellation/rejection, trusted refund
authorization, party dispute initiation, trusted dispute resolution,
idempotency, rollback, and serialized release/recovery races. These internal
states do not represent an external refund or transfer; no real payment
provider, webhook, or real-money operation is implemented.

**Product/provider prerequisite:** No authorization-expiration duration or
policy is established by current product rules. Do not expire holds on an
invented timer. Define authorization validity, provider reversal semantics, and
the handling of stale holds before a provider integration relies on expiration.

##### Collaboration Funds-Hold Task 5

**COMPLETE — provider boundary only.** The provider contract and normalized
results/errors live under `backend/app/payments/`; the collaboration payment
service validates provider identity, reference, amount, currency, expected
status, idempotency, and internal relationship before applying an existing
state-machine operation. Migration 214's provider/reference pair and uniqueness
constraint are sufficient; no migration 218 was needed.

`COLLABORATION_PAYMENT_PROVIDER` defaults to `disabled`; `fake` selects the
in-memory, no-network adapter in non-production environments. Production
rejects the fake provider, and `COLLABORATION_PAYMENT_REAL_MONEY_ENABLED=true`
is rejected because no real-money adapter exists. No provider credentials are
defined or logged. Provider status reads do not mutate internal state. The
normalized event envelope is prepared for a future persistent replay-safe
consumer; no webhook endpoint or event processor exists. No payment frontend,
capture/release, or external refund operation is enabled.

##### Final readiness decision

##### Beta security gates: configuration and sensitive logging

| Gate | Status | Focused evidence / limits |
|------|--------|--------------------------|
| Production configuration | **VERIFIED** | 56 focused tests in [test_production_config.py](../backend/tests/test_production_config.py) passed; `git diff --check` passed. Production configuration fails closed for missing/unsafe required dependency settings. DEBUG parsing accepts normal boolean representations, rejects invalid inputs clearly, and prohibits debug mode in production. |
| Sensitive logging | **VERIFIED** | 16 focused tests in [test_exception_logging.py](../backend/tests/test_exception_logging.py) and [test_alpha_health_errors.py](../backend/tests/test_alpha_health_errors.py) passed. [Global exception handlers](../backend/app/errors.py) and [database-connection logging](../backend/app/db/session.py) no longer log raw exception text, tracebacks, request paths, or sensitive error details; API responses and database behavior remain unchanged. |

These are focused code-level results, not deployment certification. Production
checks require explicit `ENVIRONMENT=production`; staging does not inherit
them. JWT length and known unsafe credentials are checked, not secret entropy
or every weak credential. Dependency TLS remains an operator requirement.
Approved production secrets/dependency URLs, TLS/HTTPS and exact approved
origins, trusted proxy configuration, a rate-limit topology decision,
proxy/APM/access-log verification, and an actual beta PostgreSQL backup/restore
drill remain **REQUIRED BEFORE BETA**. No actual beta backup or restore was
performed during these gates.

##### Beta Readiness Gate 1: media/storage

**BLOCKED / NOT CERTIFIED.** Media/storage implementation is **COMPLETE**;
23 focused media/storage tests passed. The live S3/storage smoke test remains
**BLOCKED** because required approved storage configuration and endpoint are
unavailable. The active FastAPI
media path continues to use the existing `services/media_storage.py` boto3
S3/S3-compatible adapter; there is no newly selected cloud provider. The
required beta values are `AWS_S3_BUCKET`, `AWS_REGION`,
`AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY`. `AWS_ENDPOINT_URL` is
optional and must identify an approved S3-compatible endpoint; leave it empty
only for an approved AWS S3 bucket. Upload limits are configurable through
`MEDIA_MAX_IMAGE_BYTES` and `MEDIA_MAX_VIDEO_BYTES` (defaults: 8 MiB and
512 MiB). The example environment file intentionally contains no storage
credentials or bucket value.

Uploads validate file signatures, enforce the configured limits, use a
server-generated owner/media key, and return the existing internal media URL;
content requests remain authorized before redirecting to a short-lived
presigned URL. Storage errors use safe generic API messages. Partial uploads
and database-persistence failures trigger object cleanup. Media, post, and
account deletion remove storage objects before committing record deletion;
retrying cleanup is safe, and failures leave database references available for
retry. The active path does not generate thumbnails or compress/transcode
media; the existing thumbnail fields remain optional and new uploads are not
marked processed until such a pipeline exists.

The required storage settings were absent at verification time, so no live
smoke test was attempted. Supply approved nonproduction values, then upload a
small disposable object, verify its short-lived retrieval reference, delete
it, and confirm cleanup before clearing Gate 1. Beta launch remains blocked
until that live check and the other existing beta gates are complete.

##### Beta Readiness Gate 2C: Docker/container

**BLOCKED.** The availability-only check found Docker CLI unavailable.
No Docker installation was attempted and Docker files were not altered during
these gates. Container build/start/health/migration/shutdown certification
remains **BLOCKED**; no container certification is claimed.

## NOT BETA READY

This decision is driven by the still-open **BLOCKER** items ALPHA-BE-010
(unverified live storage contract) and ALPHA-BE-019 (no intended-container
build/run validation). The account-deletion privacy integration, deployment
secrets/TLS/network setup, downstream credential-log handling, actual beta
database assessment, backup/restore drill, and topology decision are also
required before beta. No blocker has been downgraded to obtain a ready status.

**Week 8 is COMPLETE as a workstream:** Tasks 1-5 evidence and register
reconciliation are recorded. **Beta launch approval is withheld** until the
blockers and required environment gates above are satisfied. No post-beta task
has started. Collaboration Funds-Hold Task 5 is complete as a provider boundary
only; no real provider integration or financial QA work is claimed.

## Questions?

If you have questions or need help:
- Check existing [issues](https://github.com/your-org/ConnextionZ/issues)
- Join our [Discord community](https://discord.gg/your-invite-link)
- Reach out to maintainers via [email](mailto:maintainers@connextionz.com)

Thank you for contributing to ConnextionZ! 🚀
