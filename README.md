# Kalpi Portfolio Execution Platform

A broker-neutral FastAPI service that converts a desired portfolio or explicit rebalance
instructions into auditable orders. It includes five Indian broker adapters, encrypted
credential storage, idempotent execution, partial-failure reporting, webhook notifications,
a deterministic demo broker, a small browser UI, tests, and a point-in-time financial-data
architecture document.

> **Safety:** real order submission is disabled by default. The mock broker is the supported
> demonstration path. Do not enable live trading until the relevant adapter has been tested
> with your own sandbox/account, instrument master, compliance requirements, and broker
> credentials. This project is an engineering exercise, not investment advice.

## Run it

### Docker (recommended)

```bash
cp .env.example .env
# Replace ENCRYPTION_SECRET in .env.
docker compose up --build
```

Open:

- UI: <http://localhost:8000>
- OpenAPI/Swagger: <http://localhost:8000/docs>
- Readiness: <http://localhost:8000/health/ready>

The Compose volume persists the SQLite audit store across restarts. To stop the service
without deleting data, run `docker compose down`.

### Local Python

Python 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Environment variables are read directly from the process; export the values in `.env` when
running outside Compose. Never commit `.env`.

## Quick demo

Connect the mock broker with eight TCS shares:

```bash
curl -X POST http://localhost:8000/api/v1/brokers/mock/connect \
  -H 'Content-Type: application/json' \
  -d '{
    "credentials": {
      "holdings": [{"symbol":"TCS","exchange":"NSE","quantity":8}]
    },
    "validate_connection": true
  }'
```

Submit a target portfolio. The engine sells TCS first, then buys INFY:

```bash
curl -X POST http://localhost:8000/api/v1/executions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-target-001' \
  -d '{
    "broker":"mock",
    "mode":"FIRST_TIME",
    "target_portfolio":[
      {"symbol":"TCS","exchange":"NSE","quantity":5},
      {"symbol":"INFY","exchange":"NSE","quantity":4}
    ]
  }'
```

Submit explicit rebalance instructions:

```bash
curl -X POST http://localhost:8000/api/v1/executions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-rebalance-001' \
  -d '{
    "broker":"mock",
    "mode":"REBALANCE",
    "instructions":[
      {"action":"ADJUST_SELL","symbol":"TCS","exchange":"NSE","quantity":2},
      {"action":"BUY_NEW","symbol":"HDFCBANK","exchange":"NSE","quantity":3}
    ]
  }'
```

Mock symbols beginning with `FAIL` are rejected. Symbols beginning with `RATE` return one
rate-limit error and then succeed, making partial failures and retry behavior easy to demo.

## Execution behavior

### Target portfolio (`FIRST_TIME`)

The planner obtains current broker holdings and computes, per `(exchange, symbol)`:

```text
delta = target quantity - current quantity
delta > 0 => BUY; delta < 0 => SELL; delta = 0 => no order
```

Positions held but absent from the desired state have a target of zero. Although the prompt
describes an empty first-time account, this generalized behavior prevents an unexpected
existing holding from being ignored.

### Explicit rebalance (`REBALANCE`)

The provided `SELL`, `BUY_NEW`, `ADJUST_SELL`, and `ADJUST_BUY` quantities are translated
directly; no delta is recalculated. Aggregate sells are checked against holdings. Duplicate
or conflicting repeated actions are rejected. All sells execute before buys so released
capital can be available to the purchase leg.

Each request must include an `Idempotency-Key`. Repeating a completed request returns its
original result instead of placing orders twice. A broker-visible client reference is also
attached where supported. An order is retried with bounded exponential backoff only for a
known rate-limit or safe transient error. Timeout/transport failure during order submission
is treated as an uncertain outcome and is **not** automatically retried, because the broker
may already have accepted the trade.

Execution outcomes are `COMPLETED`, `PARTIALLY_COMPLETED`, `FAILED`, or `NO_ACTION`. An
accepted order is not represented as filled unless the broker's status endpoint confirms it.

## Broker adapters

`BrokerAdapter` defines credential validation, holdings, order placement, status lookup, and
cleanup. The execution engine knows only this interface. A sixth broker requires a new class
and one registry entry in `app/brokers/factory.py`; planner and engine code remain unchanged.

| Adapter | Session fields expected | Symbol metadata |
|---|---|---|
| Zerodha | `api_key`, `access_token` | trading symbol |
| FYERS | `client_id`, `access_token` | trading symbol |
| Angel One | `api_key`, `access_token`, optional IP/MAC fields | `instrument_tokens` map |
| Groww | `access_token` | trading symbol |
| Upstox | `access_token` | `instrument_keys` map |

Example connection body:

```json
{
  "credentials": {
    "access_token": "broker-issued-session-token",
    "instrument_keys": {"NSE:RELIANCE": "NSE_EQ|INE002A01018"}
  },
  "validate_connection": true
}
```

The endpoint accepts an already-issued broker session rather than collecting a customer's
password or TOTP. This is intentional: login and consent should remain on the broker's page.
In a consumer product, an OAuth callback service would exchange the authorization code and
pass the resulting session into the same adapter factory. Angel One deployments also need
the broker's currently required network identity/static-IP setup.

The implementations use the brokers' documented REST contracts rather than a normalization
library. This keeps payload translation visible for review, avoids placing a community
dependency in the order path, and demonstrates the adapter pattern directly. Relevant
official references are [Kite orders](https://kite.trade/docs/connect/v3/orders/),
[FYERS API documentation](https://myapi.fyers.in/docsv3),
[Angel One SmartAPI](https://smartapi.angelone.in/docs),
[Groww orders](https://groww.in/trade-api/docs/curl/orders), and
[Upstox order V3](https://upstox.com/developer/api-documentation/v3/place-order/).
Broker contracts and regulatory controls change; revalidate them before live use.

To permit live submission, the deployment operator must deliberately set:

```text
ENABLE_LIVE_TRADING=true
```

Credential validation and holdings reads can work while this remains false. Credentials are
encrypted before SQLite storage using a key derived from `ENCRYPTION_SECRET`; tokens are not
included in API responses or application logs.

## API summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/brokers` | Adapter/connection status |
| `POST` | `/api/v1/brokers/{broker}/connect` | Validate and encrypt a broker session |
| `DELETE` | `/api/v1/brokers/{broker}/connection` | Remove a connection |
| `POST` | `/api/v1/executions` | Plan and execute a portfolio request |
| `GET` | `/api/v1/executions/{id}` | Fetch an audit result |
| `GET` | `/api/v1/executions` | List recent audit results |

When `WEBHOOK_URL` is set, completion sends `portfolio.execution.completed` with the full
execution and summary. Without a URL, the same event is logged. Notification failure never
changes the authoritative trade result.

## Architecture

```text
FastAPI / Browser UI
        |
        v
Execution Engine -----> Notification Service -----> Webhook / structured log
        |
        +-----> Pure Order Planner
        |
        +-----> Broker Factory -----> BrokerAdapter -----> Broker API
        |
        +-----> SQLite audit and encrypted connection store
```

Separation of concerns is intentional: API validation, planning, orchestration, broker
translation, persistence, and notification can be tested and replaced independently. SQLite
is pragmatic for the single-instance assignment. At multi-instance production scale, retain
the repository boundary and move executions/credentials to PostgreSQL, use a queue-backed
worker with per-account ordering, and use a distributed idempotency/locking mechanism.

## Tests

```bash
pytest -q
```

Coverage includes delta calculation, no-op portfolios, explicit rebalance behavior,
oversell/duplicate validation, sell-before-buy ordering, idempotency, encrypted credentials,
rate-limit retry, partial failure, connection enforcement, and health checks.

## Deliberate limitations

- Live adapters are contract implementations, not claims of credentialed end-to-end
  certification. Actual accounts, subscriptions, exchange permissions, IP registration,
  depository authorization, and market-hours behavior are outside this repository.
- The prototype executes synchronously so the assignment flow is easy to inspect. Production
  execution should use durable jobs/outbox events and reconciliation workers.
- Only delivery market orders are modeled. Limit orders, funds/margin preflight, price bands,
  freeze quantities, AMO policy, and CDSL TPIN/DDPI flows require product decisions.
- This version uses one logical demo user. Production credentials and executions must be
  scoped to an authenticated tenant/user.
- SQLite is not intended for horizontally scaled writers.

## Problem statement 2

The requested 1,000–1,500-word technical design, including point-in-time fundamentals,
retention, live and historical compute, APIs, caching, and diagrams, is in
[`docs/financial-data-platform.md`](docs/financial-data-platform.md).

