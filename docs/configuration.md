# Configuration

The proxy can run as an on-site sender, a central receiver, or both roles in one process for development. Configure a
role by setting at least one URL in its direction:

- `CENTRAL_*_PROXY_URL` sends telemetry from an on-site proxy to a central proxy.
- `UPSTREAM_*_URL` sends accepted telemetry from a central proxy to its storage backend.

At least one of these four URL settings must be configured or the application will refuse to start.

## Role requirements

| Role | Enable with | Additional requirements |
|---|---|---|
| On-site metrics | `CENTRAL_PROMETHEUS_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| On-site traces | `CENTRAL_TEMPO_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| Central metrics | `UPSTREAM_PROMETHEUS_URL` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |
| Central traces | `UPSTREAM_TEMPO_URL` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |

When both signals are enabled for a role, their shared settings only need to be supplied once.

## Bittensor identity and validator discovery

| Variable | Used by | Description |
|---|---|---|
| `BITTENSOR_NETUID` | On-site | The single subnet UID represented by this deployment, for example `12`. It is included in the signed request headers. |
| `BITTENSOR_NETUIDS` | Central | Comma-separated allow-list of subnet UIDs, for example `2,12,22`. Validator membership is synchronized separately for each subnet. |
| `BITTENSOR_WALLET_DIRECTORY` | On-site | Directory containing Bittensor wallets. Defaults to `~/.bittensor/wallets`. |
| `BITTENSOR_WALLET_NAME` | On-site | Wallet name used to sign telemetry. |
| `BITTENSOR_WALLET_HOTKEY_NAME` | On-site | Wallet hotkey name used to sign telemetry and identify the validator. |
| `PYLON_ENDPOINT` | Central | Base URL for the Bittensor Pylon API used to fetch current validators. |
| `PYLON_OPEN_ACCESS_TOKEN` | Central | Optional Pylon open-access token. Leave empty only when the configured Pylon permits it. |

The central proxy authorizes a hotkey for the netuid carried in the signed `Bittensor-Netuid` header. Consequently, a
hotkey registered on one configured subnet is not automatically authorized on another.

For local development without Celery validator synchronization, add a validator explicitly:

```bash
cd app/src
pdm run manage.py debug_add_validator <hotkey> --netuid <netuid>
```

## Telemetry destinations

All URL variables are base URLs. The proxy appends the paths shown below.

| Variable | Used by | Appended path | Description |
|---|---|---|---|
| `CENTRAL_PROMETHEUS_PROXY_URL` | On-site | `/prometheus/inbound` | Central proxy that receives signed Prometheus remote-write requests. |
| `UPSTREAM_PROMETHEUS_URL` | Central | `/api/v1/write` | Prometheus server with its remote-write receiver enabled. |
| `CENTRAL_TEMPO_PROXY_URL` | On-site | `/traces/inbound` | Central proxy that receives signed OTLP trace requests. |
| `UPSTREAM_TEMPO_URL` | Central | `/v1/traces` | Tempo OTLP/HTTP receiver. |
| `ON_SITE_PROXY_URL` | Alloy | `/traces/outbound` | Base URL of the on-site Django proxy. This is consumed by Alloy, not Django. |

The provided Alloy configuration listens for application traces on OTLP/gRPC port `4317` and OTLP/HTTP port `4318`.
It tail-samples and batches traces before forwarding them to the on-site proxy.

## Storage and cache

The central role requires PostgreSQL to store validator records and Redis for caching, task coordination, and Celery.
Configure one database URL and the Redis connection:

| Variable | Description |
|---|---|
| `DATABASE_POOL_URL` | PostgreSQL connection through a pooler. |
| `DATABASE_URL` | Direct PostgreSQL connection; used when `DATABASE_POOL_URL` is empty. |
| `REDIS_HOST` | Redis hostname. |
| `REDIS_PORT` | Redis port. |
| `CELERY_BROKER_URL` | Celery broker URL, normally Redis database 0. |

## On-site example

This deployment sends both metrics and traces for netuid 12:

```dotenv
BITTENSOR_NETUID=12
BITTENSOR_WALLET_DIRECTORY=/wallets
BITTENSOR_WALLET_NAME=validator
BITTENSOR_WALLET_HOTKEY_NAME=default

CENTRAL_PROMETHEUS_PROXY_URL=https://telemetry.example.com
CENTRAL_TEMPO_PROXY_URL=https://telemetry.example.com

# Use the Compose service URL when Alloy and Django share a Docker network.
ON_SITE_PROXY_URL=http://app:8000
```

Do not configure `BITTENSOR_NETUIDS`, Pylon, a database, or Redis unless this process also acts as a central proxy.

## Central example

This deployment accepts validators from netuids 2, 12, and 22:

```dotenv
BITTENSOR_NETUIDS=2,12,22
PYLON_ENDPOINT=http://bittensor-pylon:8000
PYLON_OPEN_ACCESS_TOKEN=replace-me

UPSTREAM_PROMETHEUS_URL=http://prometheus:9090
UPSTREAM_TEMPO_URL=http://tempo:4319

DATABASE_POOL_URL=
DATABASE_URL=postgres://postgres:replace-me@db:5432/project
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_BROKER_URL=redis://redis:6379/0
```

Do not configure `CENTRAL_PROMETHEUS_PROXY_URL`, `CENTRAL_TEMPO_PROXY_URL`, or wallet settings unless this process also
acts as an on-site proxy.

## Combined development mode

The development template sets both the `CENTRAL_*_PROXY_URL` and `UPSTREAM_*_URL` variables. A single Django process
therefore performs an on-site outbound request back to its own central inbound endpoint, allowing the complete signing
and validation flow to be exercised locally. See [`envs/dev/.env.template`](../envs/dev/.env.template) for the full
example.
