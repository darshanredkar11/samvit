# Deployment Guide

## Prerequisites

- **Docker** 24+ and **Docker Compose** v2+
- **PostgreSQL** 15+ (if not using Docker)
- **Redpanda** 23+ (if not using Docker)

## Quick Start (Single Machine)

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
cp .env.example .env
docker compose up -d
curl http://127.0.0.1:8765/ready
```

## Configuration

Set these environment variables (see `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | `postgresql://samvit:samvit@localhost:5432/samvit` | PostgreSQL connection string |
| `REDPANDA_BROKERS` | Yes | `localhost:9092` | Redpanda/Kafka broker list |
| `SAMVIT_ADMIN_SECRET` | No | — | Secret for admin token resets |
| `SAMVIT_ADMIN_DEV_MODE` | No | `false` | Disable auth for admin API (dev only) |
| `SAMVIT_CORS_ORIGINS` | No | `http://localhost,http://127.0.0.1` | Allowed CORS origins |
| `SAMVIT_CODE_ROOTS` | No | `/workspace` | Directories allowed for code indexing |

## Using the CLI

```bash
# Register an agent
samvit register my-agent --provider claude-code

# List agents (admin)
samvit admin agents list

# List pending tasks
samvit admin tasks list --status pending
```

## Multi-Machine Deployment

### Machine A (hosts Samvit)
```bash
docker compose -f docker-compose.yml up -d samvit postgres redpanda
samvit register worker-pool --provider dispatcher
# Copy the returned token
```

### Machine B (Claude Code worker)
```bash
# Point Claude Code to Machine A's Samvit
export SAMVIT_URL=http://machine-a:8765
# Use the token from registration
```

## Production Hardening

1. **Set `SAMVIT_ADMIN_SECRET`** to a strong random value
2. **Disable `SAMVIT_ADMIN_DEV_MODE`** (never set to `true` in production)
3. **Restrict `SAMVIT_CORS_ORIGINS`** to your domain
4. **Use a reverse proxy** (nginx, Caddy) for TLS termination
5. **Enable connection pooling**: set `DATABASE_POOL_MIN=5` and `DATABASE_POOL_MAX=20`

## Health Checks

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness — is the service responding? |
| `GET /ready` | Readiness — is the DB migrated and ready? |
| `GET /api/metrics` | Prometheus-format metrics |

## Backup

```bash
docker compose exec postgres pg_dump -U samvit samvit > backup_$(date +%Y%m%d).sql
```

## Monitoring

Alert if:
- Guard violations > 10/day
- Rate limit hits > 5% of requests
- Task failures > 1%
- `/ready` returns non-200 for > 30s
