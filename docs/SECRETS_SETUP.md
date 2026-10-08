# Secrets Management — Setup Guide

> **Author:** Seth Nenninger (Lead Architect)
> **Last Updated:** 2026-07-08
> **Status:** Active

---

## Overview

ConnextionZ uses environment variables for all secrets and configuration. Secrets must **never** be committed to version control. This document explains how to set up secrets for local development, Docker Compose, and production deployment.

## Quick Start (Local Development)

```bash
# 1. Copy the template
cp .env.example .env

# 2. Generate URL-safe random passwords and a JWT signing key
#    On Linux/macOS:
openssl rand -hex 32

#    On Windows (PowerShell):
(-join (1..64 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) }))

# 3. Copy the root .env.example to .env and replace every required blank value
```

## Environment Variable Inventory

| Variable | Scope | Sensitivity | Notes |
|----------|-------|-------------|-------|
| `POSTGRES_PASSWORD` | DB | **Secret** | PostgreSQL superuser password |
| `REDIS_PASSWORD` | Cache | **Secret** | Redis AUTH password |
| `RABBITMQ_PASSWORD` | Queue | **Secret** | RabbitMQ broker password |
| `REDIS_COMMANDER_HTTP_PASSWORD` | Admin UI | **Secret** | Redis Commander web UI password |
| `JWT_SECRET_KEY` | Auth | **Secret** | HMAC signing key for JWT tokens |
| `OPENAI_API_KEY` | LLM | **Secret** | OpenAI API key (if using cloud LLM) |
| `ANTHROPIC_API_KEY` | LLM | **Secret** | Anthropic API key (if using cloud LLM) |
| `AWS_ACCESS_KEY_ID` | Cloud | **Secret** | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | Cloud | **Secret** | AWS IAM secret key |
| `AWS_S3_BUCKET` | Media storage | Configuration | Approved private beta bucket; required |
| `AWS_REGION` | Media storage | Configuration | Approved bucket region; required |
| `AWS_ENDPOINT_URL` | Media storage | Configuration | Optional approved S3-compatible endpoint; blank only for AWS S3 |
| `MEDIA_MAX_IMAGE_BYTES` | Media storage | Configuration | Optional positive image upload limit in bytes; defaults to 8 MiB |
| `MEDIA_MAX_VIDEO_BYTES` | Media storage | Configuration | Optional positive video upload limit in bytes; defaults to 512 MiB |

## Canonical Docker Compose Integration

The canonical beta-like deployment uses the root `docker-compose.yml` and
`Dockerfile`. Compose reads root `.env` for variable substitution; the root
`.env.example` is its matching template. The older `docker/docker-compose.yml`
and `docker/Dockerfile.backend` are legacy development scaffolding, not the
beta deployment path.

Root Compose requires `JWT_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
`RABBITMQ_USER`, `RABBITMQ_PASSWORD`, and `CORS_ORIGINS`; it supplies no
development credential defaults. Use URL-safe passwords because Compose
embeds them in the PostgreSQL, Redis, and RabbitMQ connection URLs. The API
uses `postgresql+asyncpg` at runtime and `postgresql+psycopg` for Alembic, with
dependency service hostnames `postgres`, `redis`, and `rabbitmq`.

Start the stack and apply migrations explicitly:

```bash
docker compose up --build -d
docker compose run --rm api alembic upgrade head
```

Do not commit root `.env`. The Docker build context excludes `.env` and
`.env.*`; runtime settings are injected through the Compose environment.

## Production Secrets

In production (AWS), secrets are managed via **AWS Secrets Manager** and injected into containers through:

1. **EKS**: External Secrets Operator syncs AWS Secrets Manager → Kubernetes Secrets.
2. **ECS/Fargate**: Secrets are referenced directly in the task definition.
3. **Terraform**: Secrets are created via `aws_secretsmanager_secret_version` resources (values set manually or via CI).

### Terraform Example

```hcl
resource "aws_secretsmanager_secret" "jwt" {
  name        = "connextionz/${var.environment}/jwt-secret-key"
  description = "JWT signing key for ConnextionZ ${var.environment}"
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = var.jwt_secret_key  # Set via CI variable, never in code
}
```

## Security Rules (Enforced)

1. **No secrets in code, config, or Dockerfiles** — checked by git-secrets + Checkov in CI.
2. **`.env` is git-ignored** — verified by `.gitignore` rules.
3. **`.env.example` IS committed** — serves as a template without real values.
4. **Default fallback values are dev-only** — never use defaults in staging/production.
5. **Rotate secrets on compromise** — JWT secret rotation invalidates all sessions; plan accordingly.

## LocalStack Notes

For local development, use locally approved S3-compatible credentials/configuration if exercising object storage. No storage credentials are supplied by `.env.example`; tests inject a mock client. Do NOT use real AWS credentials in local dev.

## Verifying Your Setup

```bash
# Check that .env is ignored
git status --ignored | grep .env

# Verify docker-compose resolves variables correctly
docker compose -f docker/docker-compose.yml config | grep -E "PASSWORD|SECRET|KEY"
```

The `config` command shows the resolved compose file — verify no hardcoded values appear.

---

*Update this document whenever new secrets are added to the platform.*
