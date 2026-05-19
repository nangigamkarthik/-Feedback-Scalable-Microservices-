# Feedback CQRS Starter

This repository starts a feedback management project with a real CQRS split, PostgreSQL-backed read and write stores, a RabbitMQ-backed projector flow, token login with admin and user roles, Docker packaging, and Kubernetes manifests.

## What we are building

We are building a `Feedback Management System MVP` with four small services:

- `gateway`: serves the browser UI and forwards API calls
- `auth-service`: validates demo users and issues access tokens
- `command-service`: accepts writes and stores the write model
- `projector`: consumes queued domain events and pushes them into the read side
- `query-service`: serves the read model, filters, and dashboard stats
- `postgres`: stores the write and read tables
- `rabbitmq`: transports events from the command side to the projector

## Why this first

This is the smallest useful slice that proves the architecture you wanted:

- `CQRS`: command and query logic are fully separated
- `PostgreSQL`: both models now run on a production-style relational database
- `Auth + roles`: users can only see their own feedback while admins can moderate everything
- `Event projection`: writes become events, then update the read model asynchronously
- `RabbitMQ`: command and query sides are decoupled by a real message broker
- `Docker`: every service has its own container image
- `Kubernetes`: manifests are included for deployment

I intentionally kept the Python dependencies light by using RabbitMQ's management HTTP API and a single PostgreSQL driver. For a later phase, we can move from the management API to a full AMQP client library.

## Architecture

```text
Browser UI
   |
gateway
   |--------------------> command-service -> postgres -> outbox
                                                 |
                                                 v
                                             rabbitmq
                                                 |
                                                 v
                                             projector
                                                 |
                                                 v
query-service -> postgres <-----------------------
```

## Features in this starter

- submit feedback
- login as user or admin
- view only your own feedback as a user
- moderate all feedback as an admin
- update feedback status
- queue write-side events in RabbitMQ and project them into the read model
- list feedback for admin view
- show dashboard stats
- run locally, with Docker Compose, or with Kubernetes

## Local run

If you want to keep the simpler direct handoff, open five terminals from the repo root and run:

```powershell
python -m services.auth_service.app
python -m services.query_service.app
python -m services.projector.app
python -m services.command_service.app
python -m services.gateway.app
```

Then open [http://localhost:8080](http://localhost:8080).

Demo credentials:

- `admin@feedback.local` / `admin123`
- `user@feedback.local` / `user123`

For local runs, the services default to shared in-memory SQLite so the starter can run without filesystem issues. Docker and Kubernetes use the explicit on-disk paths defined in their environment variables.

To run the RabbitMQ version locally without Docker Compose, start RabbitMQ first:

```powershell
docker run -d --name feedback-rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3.13-management
```

Then start the services with these extra environment variables in the `command-service` and `projector` terminals:

```powershell
$env:EVENT_TRANSPORT='rabbitmq'
$env:RABBITMQ_API_URL='http://127.0.0.1:15672/api'
$env:RABBITMQ_QUEUE='feedback.events'
```

If you want local PostgreSQL without Docker Compose, also start Postgres and set:

```powershell
$env:WRITE_DATABASE_URL='postgresql://feedback:feedback@127.0.0.1:5432/feedback'
$env:READ_DATABASE_URL='postgresql://feedback:feedback@127.0.0.1:5432/feedback'
```

## Docker Compose

```powershell
docker compose up --build
```

Then open [http://localhost:8080](http://localhost:8080).

RabbitMQ management UI will be available at [http://localhost:15672](http://localhost:15672) with `guest / guest`.

PostgreSQL will be available on `localhost:5432` with database `feedback` and credentials `feedback / feedback`.

## Kubernetes

Build the local images first:

```powershell
docker build -f services/query_service/Dockerfile -t feedback-query-service:latest .
docker build -f services/projector/Dockerfile -t feedback-projector:latest .
docker build -f services/command_service/Dockerfile -t feedback-command-service:latest .
docker build -f services/auth_service/Dockerfile -t feedback-auth-service:latest .
docker build -f services/gateway/Dockerfile -t feedback-gateway:latest .
```

Apply the manifest bundle:

```powershell
kubectl apply -f k8s/app.yaml
kubectl port-forward svc/gateway 8080:8080 -n feedback-system
```

Then open [http://localhost:8080](http://localhost:8080).

## Important note

The local fallback still uses SQLite for quick development, but the default containerized stack now uses PostgreSQL. Strong next steps are:

- replace the RabbitMQ management API calls with a native AMQP client
- harden auth with password resets, hashed sessions, and proper JWTs
- move to persistent storage in Kubernetes
