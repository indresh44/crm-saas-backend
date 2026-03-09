# CRM SaaS for Service Businesses

## Overview

This project is a SaaS CRM system designed for **small service businesses** such as:

* event planners
* photographers
* interior designers
* wedding decorators
* marketing agencies

Most of these businesses currently manage operations using:

* WhatsApp
* Google Sheets
* paper notebooks

This system aims to provide a **simple and fast workflow tool**.

---

## Core Workflow

The primary workflow supported by the product:

Customer
↓
Lead
↓
Quote
↓
Booking
↓
Invoice
↓
Payment

This represents the full lifecycle of a client engagement.

---

## Key Principles

The system must be:

* extremely fast
* mobile friendly
* simple to use
* WhatsApp friendly
* scalable for many businesses

Performance target:

* lead creation < 100ms
* pipeline load < 300ms
* search < 100ms

---

## Technology Stack

Backend

* FastAPI
* SQLModel
* PostgreSQL
* Redis
* Alembic migrations

Frontend (future)

* Next.js
* TypeScript
* Tailwind

Infrastructure

* Docker
* AWS (future)
* Redis for caching

---

## Architecture Philosophy

The backend follows a layered architecture:

API Layer → Service Layer → Repository Layer → Database

This separation ensures:

* clean code
* testability
* maintainability
* scalability

---

## Domain Entities

Core entities in the system:

Business
User
Customer
Pipeline
PipelineStage
Lead
LeadActivity
Quote
QuoteItem
Booking
Invoice
Payment
Notification

---

## Multi-Tenant Design

The system is multi-tenant.

Every entity must belong to a business.

Example:

business_id is required in most tables.

---

## Development Practices

The following engineering practices must be followed:

* database migrations for every schema change
* service layer for business logic
* repository layer for database access
* tests for core logic
* indexes for frequently filtered fields

---

## Performance Guidelines

Important indexes must exist for:

business_id
stage_id
phone
created_at

Pagination should use **cursor pagination**.

Offset pagination should be avoided.

---

## Future Features

Future roadmap may include:

* WhatsApp integration
* automated reminders
* AI quote generation
* analytics dashboards
* workflow automation
* team collaboration

---

## AI Assistance

This project uses AI coding assistants (Codex / Copilot).

AI can be used for:

* generating production grade code
* writing migrations
* creating CRUD APIs
* generating tests

However, developers must review all AI-generated code.
