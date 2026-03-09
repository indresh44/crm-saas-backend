# Backend Architecture

## Overview

The backend is built using **FastAPI** and follows a layered architecture to ensure scalability and maintainability.

The architecture separates responsibilities across different layers.

---

## High Level Architecture

Client
↓
API Layer (FastAPI routers)
↓
Service Layer (business logic)
↓
Repository Layer (database operations)
↓
Database (PostgreSQL)

---

## Folder Structure

app/

core/
configuration, database connection, shared utilities

models/
SQLModel database models

schemas/
request and response schemas

repositories/
database queries and persistence logic

services/
business logic and orchestration

api/
FastAPI route definitions

workers/
background jobs (future)

events/
domain event system (future)

---

## Example Request Flow

Example: Creating a lead

Client → POST /leads

API Layer
Receives request and validates payload

↓

Service Layer
Handles business logic such as assigning pipeline stage

↓

Repository Layer
Creates database record

↓

Database
Lead stored in PostgreSQL

↓

Response returned to client

---

## Database Design Principles

The system uses PostgreSQL.

Important principles:

* UUID primary keys
* indexed fields for filtering
* normalized relational structure
* migrations for all schema changes

---

## Migration System

Alembic is used for database migrations.

Typical workflow:

1. update model
2. generate migration
3. review migration
4. apply migration

Commands:

alembic revision --autogenerate -m "description"

alembic upgrade head

---

## Caching Strategy

Redis will be used for caching frequently accessed data.

Examples:

* dashboard metrics
* pipeline queries
* search results

---

## Background Jobs

Background processing will be introduced later.

Use cases:

* sending notifications
* WhatsApp messages
* PDF generation
* analytics calculations

Potential tools:

* Celery
* Redis Queue

---

## Event System (Future)

Domain events may be introduced.

Examples:

lead.created
quote.sent
payment.received

These events can trigger:

* notifications
* automations
* integrations

---

## API Design Principles

RESTful conventions are followed.

Examples:

POST /leads
GET /leads
PATCH /leads/{id}

Filtering example:

GET /leads?stage_id=xxx

---

## Security

Security practices include:

* authenticated endpoints
* business isolation using business_id
* validation of request data
* protection against unauthorized access

---

## Testing Strategy

Testing is done using pytest.

Tests include:

repository tests
service tests
API tests

---

## Scaling Strategy

The application can scale horizontally.

Key strategies:

* stateless API servers
* connection pooling
* Redis caching
* database indexing

---

## Deployment (Future)

Initial deployment targets:

* Docker containers
* cloud infrastructure (AWS / Fly.io)

Components:

API server
PostgreSQL database
Redis cache
