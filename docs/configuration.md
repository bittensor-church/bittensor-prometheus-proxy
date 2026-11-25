# Configuration

The proxy can run as an on-site sender, a central receiver, or both roles in one process for development. Configure a
role by setting at least one destination in its direction:

- `CENTRAL_*_PROXY_URL` sends telemetry from an on-site proxy to a central proxy.
- `UPSTREAM_*_URL` sends accepted metrics, traces, or logs from a central proxy to its storage backend.
- `UPSTREAM_SENTRY_DSNS` sends accepted error envelopes from a central proxy to Sentry. Its value is a JSON object of Sentry DSNs.

At least one of these must be configured or the application will refuse to start.

## Role requirements

| Role | Enable with | Additional requirements |
|---|---|---|
| On-site metrics | `CENTRAL_PROMETHEUS_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| On-site traces | `CENTRAL_TEMPO_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| On-site errors | `CENTRAL_SENTRY_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| On-site logs | `CENTRAL_LOKI_PROXY_URL` | `BITTENSOR_NETUID` and wallet configuration |
| Central metrics | `UPSTREAM_PROMETHEUS_URL` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |
| Central traces | `UPSTREAM_TEMPO_URL` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |
| Central errors | `UPSTREAM_SENTRY_DSNS` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |
| Central logs | `UPSTREAM_LOKI_URL` | `BITTENSOR_NETUIDS`, Pylon, PostgreSQL, and Redis |

When several signals are enabled for a role, their shared settings only need to be supplied once.

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

URL variables are base URLs. The proxy appends the paths shown below. `UPSTREAM_SENTRY_DSNS` is a JSON map of full
DSNs, and the proxy derives each project's envelope URL from its DSN.

| Variable | Used by | Appended path | Description |
|---|---|---|---|
| `CENTRAL_PROMETHEUS_PROXY_URL` | On-site | `/prometheus/inbound` | Central proxy that receives signed Prometheus remote-write requests. |
| `UPSTREAM_PROMETHEUS_URL` | Central | `/api/v1/write` | Prometheus server with its remote-write receiver enabled. |
| `CENTRAL_TEMPO_PROXY_URL` | On-site | `/traces/inbound` | Central proxy that receives signed OTLP trace requests. |
| `UPSTREAM_TEMPO_URL` | Central | `/v1/traces` | Tempo OTLP/HTTP receiver. |
| `CENTRAL_SENTRY_PROXY_URL` | On-site | `/sentry/inbound` | Central proxy that receives signed Sentry envelopes. |
| `UPSTREAM_SENTRY_DSNS` | Central | `/api/<project_id>/envelope/` | JSON map of netuid to Sentry DSN. The envelope path comes from the DSN's project id. |
| `CENTRAL_LOKI_PROXY_URL` | On-site | `/loki/inbound` | Central proxy that receives signed Loki push requests. |
| `UPSTREAM_LOKI_URL` | Central | `/loki/api/v1/push` | Loki server. Basic auth, when required, is `user:password` in this URL. |
| `ON_SITE_PROXY_URL` | Alloy | `/traces/outbound`, `/loki/outbound` | Base URL of the on-site Django proxy. This is consumed by Alloy, not Django. |

The provided Alloy configuration listens for application traces on OTLP/gRPC port `4317` and OTLP/HTTP port `4318`.
It tail-samples and batches traces before forwarding them to the on-site proxy. A second Alloy reads container logs
and pushes them to `{ON_SITE_PROXY_URL}/loki/outbound`.

## Logs

On-site Alloy keeps the container name and log stream labels from the cookiecutter log pipeline, plus `client` and
`client_server_group`. It posts each batch to the on-site proxy and retries from the response it gets back.

The on-site proxy adds `hotkey` and `netuid` to every stream, signs the snappy protobuf body, and forwards it once to
`{CENTRAL_LOKI_PROXY_URL}/loki/inbound`. The central proxy checks the signature and those labels, then writes the same
body to Loki. One Loki URL serves every netuid; filter on the labels.

```dotenv
UPSTREAM_LOKI_URL=https://loki.example.com
```

When that Loki requires basic auth, put the credentials in the URL: `https://user:password@loki.example.com`.

## Sentry

On-site processes set their Sentry DSN to the on-site proxy. Any public key and project id are accepted. A process on
the same Docker network uses a DSN such as `http://public@bittensor-prometheus-proxy:8000/1`. The SDK posts envelopes
to `http://bittensor-prometheus-proxy:8000/api/1/envelope/`.

The on-site proxy writes `hotkey` and `netuid` into each event's tags, signs the envelope, and forwards it once to
`{CENTRAL_SENTRY_PROXY_URL}/sentry/inbound`. `sentry_sdk` performs the retries.

The central proxy accepts envelope items of type `event`, `attachment`, and `client_report`. An attachment must be an
ordinary file: `attachment_type` omitted or `event.attachment`, and a filename other than `__sentry-event` or
`__sentry-breadcrumb*`. It checks that every event's `hotkey` and `netuid` tags match the signed headers, then
forwards the envelope to the DSN for that netuid.
`UPSTREAM_SENTRY_DSNS` is a JSON object whose keys are netuids:

```dotenv
UPSTREAM_SENTRY_DSNS={"12":"https://<public_key>@sentry.reef.pl/<project_id>"}
```

A signed request for an allowed netuid with no entry in that map is rejected. `SENTRY_DSN` reports errors from this
proxy process. Validator events go to the projects listed in `UPSTREAM_SENTRY_DSNS`.

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

This deployment sends metrics, traces, logs, and errors for netuid 12:

```dotenv
BITTENSOR_NETUID=12
BITTENSOR_WALLET_DIRECTORY=/wallets
BITTENSOR_WALLET_NAME=validator
BITTENSOR_WALLET_HOTKEY_NAME=default

CENTRAL_PROMETHEUS_PROXY_URL=https://telemetry.example.com
CENTRAL_TEMPO_PROXY_URL=https://telemetry.example.com
CENTRAL_SENTRY_PROXY_URL=https://telemetry.example.com
CENTRAL_LOKI_PROXY_URL=https://telemetry.example.com

# Use the Compose service URL when Alloy and Django share a Docker network.
ON_SITE_PROXY_URL=http://app:8000
# Processes on this network use http://public@app:8000/1 as their Sentry DSN.
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
UPSTREAM_SENTRY_DSNS={"2":"https://<public_key>@sentry.reef.pl/<project_id>","12":"https://<public_key>@sentry.reef.pl/<project_id>","22":"https://<public_key>@sentry.reef.pl/<project_id>"}
UPSTREAM_LOKI_URL=https://loki.example.com

DATABASE_POOL_URL=
DATABASE_URL=postgres://postgres:replace-me@db:5432/project
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_BROKER_URL=redis://redis:6379/0
```

Do not configure `CENTRAL_PROMETHEUS_PROXY_URL`, `CENTRAL_TEMPO_PROXY_URL`, `CENTRAL_SENTRY_PROXY_URL`,
`CENTRAL_LOKI_PROXY_URL`, or wallet settings unless this process also acts as an on-site proxy.

## Combined development mode

The development template sets the `CENTRAL_*_PROXY_URL` variables and the metrics, traces, and logs upstream URLs. A
single Django process therefore performs an on-site outbound request back to its own central inbound endpoint, allowing
the complete signing and validation flow to be exercised locally. `UPSTREAM_LOKI_URL` points at the local Loki on port
`3100`. `UPSTREAM_SENTRY_DSNS` is `{}` in that template, so central Sentry forwarding stays disabled until it contains a
real project DSN, such as one on sentry.reef.pl. The development stack does not run Sentry. See
[`envs/dev/.env.template`](../envs/dev/.env.template) for the full example.
