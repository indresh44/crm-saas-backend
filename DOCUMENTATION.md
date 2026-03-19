# CRM SaaS Backend - Comprehensive Documentation

## Table of Contents

1. [Project Overview](#project-overview)
2. [Current Project Status](#current-project-status)
3. [Technology Stack](#technology-stack)
4. [Project Structure](#project-structure)
5. [Architecture](#architecture)
6. [Data Models](#data-models)
7. [API Endpoints](#api-endpoints)
8. [Services Layer](#services-layer)
9. [Repository Layer](#repository-layer)
10. [Database Design](#database-design)
11. [Workflow](#workflow)
12. [Configuration](#configuration)
13. [Development Guide](#development-guide)
14. [Summary](#summary)

---

## Project Overview

### Purpose

This is a **SaaS CRM system** designed for small service businesses such as:

- Event planners
- Photographers
- Interior designers
- Wedding decorators
- Marketing agencies

### Key Goals

- Extremely fast performance (lead creation < 100ms, pipeline load < 300ms, search < 100ms)
- Mobile-friendly interface
- Simple and intuitive to use
- WhatsApp integration support
- Scalable for multiple businesses

---

## Current Project Status

Snapshot date: **2026-03-19**

### Implemented and Available

- Layered backend architecture is in place (`api -> services -> repositories -> models`).
- Core CRM APIs are implemented for businesses, users, customers, leads, lead activities, lead followups, pipelines, pipeline stages, quotes, bookings, invoices, payments, tasks, attachments, messages, and notifications.
- WhatsApp APIs are implemented for account management, conversations, messaging, and webhooks.
- Health and API docs endpoints are available (`/health`, `/api/docs`, `/api/redoc`).
- Alembic migrations through `0010_invoice_seq_fix` are present in the repository.
- Attachment uploads are supported through Cloudflare R2.
- Invoice numbers are generated per business using a business-local sequence.
- Payment recording updates invoice status automatically (`SENT`, `PARTIAL`, `PAID`, `OVERDUE`).

### Pending / Not Yet Implemented

- Automated test suite is not present yet (no `tests/` directory in the repository).
- Redis is provisioned in `docker-compose.yml` but not yet integrated in application code for caching/background workflows.

---

## Technology Stack

### Backend

- **Framework**: FastAPI (modern, fast Python web framework)
- **ORM**: SQLModel (combines Pydantic and SQLAlchemy)
- **Database**: PostgreSQL (relational database)
- **Migrations**: Alembic (database schema versioning)
- **Server**: Uvicorn (ASGI server)
- **Environment**: Python 3.8+

### Key Dependencies

```
fastapi          - Web framework
uvicorn          - ASGI server
sqlmodel         - ORM and data validation
psycopg2-binary  - PostgreSQL adapter
alembic          - Database migrations
python-dotenv    - Environment variables
pydantic-settings - Configuration management
pywa              - WhatsApp integration
boto3             - Cloudflare R2 / S3-compatible storage
python-multipart  - Multipart file upload parsing
```

### Infrastructure

- **Containerization**: Docker
- **Composition**: Docker Compose
- **Future**: AWS deployment, Redis caching

---

## Project Structure

```
crm-saas-backend/
├── app/                              # Main application package
│   ├── __init__.py
│   ├── main.py                       # FastAPI app initialization
│   ├── core/                         # Core utilities and configuration
│   │   ├── __init__.py
│   │   ├── config.py                 # Settings and environment configuration
│   │   ├── database.py               # Database connection and session management
│   │   ├── dependencies.py           # FastAPI dependency injection
│   │   └── storage.py                # Cloudflare R2 storage helper
│   ├── models/                       # SQLModel database models
│   │   ├── __init__.py
│   │   ├── common.py                 # Base model mixins (UUID, timestamps)
│   │   ├── enums.py                  # Enumeration types
│   │   ├── user.py                   # User model
│   │   ├── business.py               # Business model
│   │   ├── customer.py               # Customer model
│   │   ├── lead.py                   # Lead and LeadActivity models
│   │   ├── pipeline.py               # Pipeline and PipelineStage models
│   │   ├── quote.py                  # Quote model
│   │   ├── booking.py                # Booking model
│   │   ├── invoice.py                # Invoice model
│   │   ├── payment.py                # Payment model
│   │   ├── task.py                   # Task model
│   │   ├── message.py                # Message model
│   │   ├── notification.py           # Notification model
│   │   └── attachment.py             # Attachment model
│   ├── repositories/                 # Data access layer
│   │   ├── __init__.py
│   │   ├── user_repository.py
│   │   ├── business_repository.py
│   │   ├── customer_repository.py
│   │   ├── lead_repository.py
│   │   ├── pipeline_repository.py
│   │   ├── quote_repository.py
│   │   ├── booking_repository.py
│   │   ├── invoice_repository.py
│   │   ├── payment_repository.py
│   │   ├── task_repository.py
│   │   ├── message_repository.py
│   │   ├── notification_repository.py
│   │   └── attachment_repository.py
│   ├── services/                     # Business logic layer
│   │   ├── __init__.py
│   │   ├── user_service.py
│   │   ├── business_service.py
│   │   ├── customer_service.py
│   │   ├── lead_service.py
│   │   ├── lead_activity_service.py
│   │   ├── lead_followup_service.py
│   │   ├── pipeline_service.py
│   │   ├── pipeline_board_service.py
│   │   ├── quote_service.py
│   │   ├── booking_service.py
│   │   ├── invoice_service.py
│   │   ├── payment_service.py
│   │   ├── task_service.py
│   │   ├── message_service.py
│   │   ├── notification_service.py
│   │   └── attachment_service.py
│   └── api/                          # API routes
│       └── v1/                       # API version 1
│           ├── __init__.py
│           ├── users.py              # User endpoints
│           ├── businesses.py         # Business endpoints
│           ├── customers.py          # Customer endpoints
│           ├── leads.py              # Lead endpoints
│           ├── lead_activities.py    # Lead activity endpoints
│           ├── lead_followups.py     # Lead followup endpoints
│           ├── pipelines.py          # Pipeline endpoints
│           ├── pipeline_stages.py    # Pipeline stage endpoints
│           ├── quotes.py             # Quote endpoints
│           ├── bookings.py           # Booking endpoints
│           ├── invoices.py           # Invoice endpoints
│           ├── payments.py           # Payment endpoints
│           ├── tasks.py              # Task endpoints
│           ├── messages.py           # Message endpoints
│           ├── notifications.py      # Notification endpoints
│           ├── attachments.py        # Attachment endpoints
│           ├── whatsapp_accounts.py  # WhatsApp account endpoints
│           ├── whatsapp_conversations.py
│           ├── whatsapp_messages.py
│           └── whatsapp_webhooks.py
├── migrations/                       # Alembic database migrations
│   ├── env.py                        # Migration environment configuration
│   ├── script.py.mako                # Migration template
│   └── versions/                     # Individual migration files
├── alembic.ini                       # Alembic configuration
├── docker-compose.yml                # Docker compose configuration
├── requirements.txt                  # Python dependencies
├── PROJECT_CONTEXT.md                # Project context and principles
├── ARCHITECTURE.md                   # Architecture documentation
└── DOCUMENTATION.md                  # This file
```

---

## Architecture

### Layered Architecture Pattern

The backend follows a **4-layer architecture** for clean separation of concerns:

```
┌─────────────────────────────────────┐
│     API Layer (FastAPI)             │
│  - HTTP endpoints                   │
│  - Input validation                 │
│  - Response formatting              │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│     Service Layer                   │
│  - Business logic                   │
│  - Data validation                  │
│  - Cross-entity operations          │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│     Repository Layer                │
│  - Database queries                 │
│  - CRUD operations                  │
│  - Query optimization               │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│  Database Layer (PostgreSQL)        │
│  - Data persistence                 │
│  - Relationships                    │
└─────────────────────────────────────┘
```

### Benefits of This Architecture

- **Separation of Concerns**: Each layer has a single responsibility
- **Testability**: Easy to unit test each layer independently
- **Maintainability**: Clear code organization and dependencies
- **Scalability**: Easy to extend with new features
- **Reusability**: Services can be reused across multiple endpoints

---

## Data Models

### Base Mixins

#### UUIDPrimaryKeyMixin

All entities use **UUID** as primary key for scalability:

```python
id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
```

#### CreatedAtMixin

Automatically tracks when a record was created:

```python
created_at: datetime = Field(default_factory=utcnow, nullable=False)
```

#### UpdatedAtMixin

Automatically tracks when a record was last updated:

```python
updated_at: datetime = Field(
    default_factory=utcnow,
    nullable=False,
    sa_column_kwargs={"onupdate": utcnow},
)
```

### Entity Models

#### User

Represents a team member or staff of a business.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `email` (str): Email address (unique per business)
- `phone` (str): Phone number
- `name` (str): Full name
- `role` (UserRole): Owner, Manager, or Staff
- `created_at` (datetime): Creation timestamp

**Enums:**

```python
class UserRole(str, Enum):
    OWNER = "owner"      # Full access to business
    MANAGER = "manager"  # Manage team and operations
    STAFF = "staff"      # Limited access
```

#### Business

Represents a business account in the system.

**Fields:**

- `id` (UUID): Primary key
- `name` (str): Business name
- `phone` (str): Business phone
- `whatsapp_number` (str, optional): WhatsApp business number
- `invoice_sequence` (int): Current per-business invoice counter
- `owner_user_id` (UUID, optional): Foreign key to User (business owner)
- `created_at` (datetime): Creation timestamp

**Purpose:** Multi-tenant isolation - each business operates independently with isolated data.

#### Customer

Represents a potential or existing client of a business.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `name` (str): Customer name
- `phone` (str): Phone number
- `email` (str, optional): Email address
- `notes` (str, optional): Additional information
- `created_at` (datetime): Creation timestamp

**Indexes:**

- `ix_customers_business_phone`: (business_id, phone) for fast lookup

#### Lead

Represents a sales lead or potential opportunity.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `customer_id` (UUID): Foreign key to Customer
- `stage_id` (UUID): Foreign key to PipelineStage
- `title` (str): Lead title/description
- `source` (str, optional): How the lead was acquired
- `event_date` (date, optional): Planned event/service date
- `estimated_value` (Decimal, optional): Estimated contract value
- `assigned_to` (UUID, optional): Foreign key to User (assigned team member)
- `notes` (str, optional): Additional notes
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

**Indexes:**

- `ix_leads_business_stage`: (business_id, stage_id) for pipeline views
- `ix_leads_business_created`: (business_id, created_at) for timeline views
- `ix_leads_business_customer`: (business_id, customer_id) for customer leads

#### LeadActivity

Records interactions and activities associated with a lead.

**Fields:**

- `id` (UUID): Primary key
- `lead_id` (UUID): Foreign key to Lead
- `type` (LeadActivityType): Type of activity
- `description` (str): Activity details
- `created_by` (UUID): Foreign key to User
- `created_at` (datetime): Activity timestamp

**Activity Types:**

```python
class LeadActivityType(str, Enum):
    CALL = "call"              # Phone call
    WHATSAPP = "whatsapp"      # WhatsApp message
    MEETING = "meeting"        # In-person or video meeting
    NOTE = "note"              # Internal note
    STATUS_CHANGE = "status_change"  # Lead moved to different stage
```

#### Pipeline

Represents a sales pipeline (customizable workflow stages).

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `name` (str): Pipeline name (e.g., "Wedding Services", "Photography")
- `created_at` (datetime): Creation timestamp

**Purpose:** Allows businesses to have multiple pipelines for different service types.

#### PipelineStage

Represents a stage within a pipeline.

**Fields:**

- `id` (UUID): Primary key
- `pipeline_id` (UUID): Foreign key to Pipeline
- `name` (str): Stage name (e.g., "Inquiry", "Proposal", "Booked")
- `position` (int): Display order in pipeline
- `color` (str): Hex color for UI representation

#### Quote

Represents a formal quote/proposal sent to a customer.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `lead_id` (UUID): Foreign key to Lead
- `quote_number` (str): Unique quote identifier
- `total_amount` (Decimal): Quote total
- `status` (QuoteStatus): Current quote status
- `issued_date` (date): When quote was issued
- `expires_date` (date): Quote expiration date
- `created_at` (datetime): Creation timestamp

**Quote Status:**

```python
class QuoteStatus(str, Enum):
    DRAFT = "draft"          # Not yet sent
    SENT = "sent"            # Sent to customer
    ACCEPTED = "accepted"    # Customer accepted
    REJECTED = "rejected"    # Customer declined
```

#### Booking

Represents a confirmed service booking.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `lead_id` (UUID): Foreign key to Lead
- `booking_date` (date): Scheduled service date
- `status` (BookingStatus): Current booking status
- `notes` (str, optional): Booking details
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

**Booking Status:**

```python
class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"      # Confirmed with customer
    IN_PROGRESS = "in_progress"  # Service in progress
    COMPLETED = "completed"      # Service completed
    CANCELLED = "cancelled"      # Booking cancelled
```

#### Invoice

Represents a billing invoice for a booking or lead.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `booking_id` (UUID, optional): Foreign key to Booking
- `lead_id` (UUID, optional): Foreign key to Lead
- `invoice_number` (str): Unique invoice identifier
- `total_amount` (Decimal): Invoice total
- `status` (InvoiceStatus): Payment status
- `issued_date` (date): Invoice issuance date
- `due_date` (date): Payment due date
- `created_at` (datetime): Creation timestamp

**Invoice Status:**

```python
class InvoiceStatus(str, Enum):
    DRAFT = "draft"          # Not yet sent
    SENT = "sent"            # Sent to customer
    PAID = "paid"            # Fully paid
    PARTIAL = "partial"      # Partially paid
    OVERDUE = "overdue"      # Past due date
```

#### Payment

Records a payment received for an invoice.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `invoice_id` (UUID): Foreign key to Invoice
- `amount` (Decimal): Payment amount
- `payment_method` (PaymentMethod): Payment method used
- `payment_date` (date): Payment date
- `reference` (str, optional): Transaction reference
- `created_at` (datetime): Payment timestamp

**Payment Methods:**

```python
class PaymentMethod(str, Enum):
    UPI = "upi"                    # Unified Payments Interface
    CASH = "cash"                  # Physical cash
    BANK_TRANSFER = "bank_transfer"  # Bank transfer
    CARD = "card"                  # Credit/debit card
```

#### Task

Represents a task or action item for team members.

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `title` (str): Task title
- `assigned_to` (UUID): Foreign key to User
- `status` (TaskStatus): Task status
- `due_date` (date, optional): Due date
- `priority` (int): Priority level
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

**Task Status:**

```python
class TaskStatus(str, Enum):
    PENDING = "pending"            # Not started
    IN_PROGRESS = "in_progress"    # Currently working on
    DONE = "done"                  # Completed
```

#### Message

Records communications (WhatsApp, SMS, Email).

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `contact_id` (UUID): Customer contact
- `content` (str): Message content
- `channel` (MessageChannel): Communication channel
- `direction` (MessageDirection): Incoming or outgoing
- `created_at` (datetime): Message timestamp

**Message Channels:**

```python
class MessageChannel(str, Enum):
    WHATSAPP = "whatsapp"  # WhatsApp messages
    SMS = "sms"            # SMS text messages
    EMAIL = "email"        # Email messages
```

**Message Direction:**

```python
class MessageDirection(str, Enum):
    INCOMING = "incoming"  # Received from customer
    OUTGOING = "outgoing"  # Sent to customer
```

#### Notification

Represents a notification to a user.

**Fields:**

- `id` (UUID): Primary key
- `user_id` (UUID): Foreign key to User
- `title` (str): Notification title
- `message` (str): Notification content
- `is_read` (bool): Read status
- `created_at` (datetime): Notification timestamp

#### Attachment

Represents file attachments (documents, images, etc.).

**Fields:**

- `id` (UUID): Primary key
- `business_id` (UUID): Foreign key to Business
- `entity_type` (AttachmentEntityType): What entity it's attached to
- `entity_id` (UUID): ID of the entity
- `filename` (str): Original filename
- `file_url` (str): Storage URL/path
- `file_size` (int): File size in bytes
- `created_at` (datetime): Upload timestamp

**Attachment Entity Types:**

```python
class AttachmentEntityType(str, Enum):
    LEAD = "lead"          # Attached to a lead
    PAYMENT = "payment"    # Attached to a payment
    QUOTE = "quote"        # Attached to a quote
    INVOICE = "invoice"    # Attached to an invoice
    TASK = "task"          # Attached to a task
```

---

## API Endpoints

The API is organized by resource type. All endpoints are prefixed with `/api/v1`.

### Users

```
POST   /api/v1/users                    Create a user
GET    /api/v1/users                    List all users in business
GET    /api/v1/users/{user_id}          Get user details
PATCH  /api/v1/users/{user_id}          Update user
DELETE /api/v1/users/{user_id}          Delete user
```

### Businesses

```
POST   /api/v1/businesses               Create a business
GET    /api/v1/businesses               Get current business
PATCH  /api/v1/businesses/{id}          Update business
```

### Customers

```
POST   /api/v1/customers                Create a customer
GET    /api/v1/customers                List customers
GET    /api/v1/customers/{customer_id}  Get customer details
PATCH  /api/v1/customers/{customer_id}  Update customer
DELETE /api/v1/customers/{customer_id}  Delete customer
```

### Leads

```
POST   /api/v1/leads                    Create a lead
GET    /api/v1/leads                    List all leads (for business)
GET    /api/v1/leads/{lead_id}          Get lead details
PATCH  /api/v1/leads/{lead_id}          Update lead
POST   /api/v1/leads/{lead_id}/move     Move lead to different stage
```

### Lead Activities

```
POST   /api/v1/lead_activities          Create activity
GET    /api/v1/lead_activities          List activities
GET    /api/v1/lead_activities/{id}     Get activity details
```

### Pipelines

```
POST   /api/v1/pipelines                Create a pipeline
GET    /api/v1/pipelines                List pipelines (for business)
GET    /api/v1/pipelines/{pipeline_id}  Get pipeline details
PATCH  /api/v1/pipelines/{pipeline_id}  Update pipeline
DELETE /api/v1/pipelines/{pipeline_id}  Delete pipeline
```

### Pipeline Stages

```
POST   /api/v1/pipeline_stages          Create a stage
GET    /api/v1/pipeline_stages          List stages (for pipeline)
GET    /api/v1/pipeline_stages/{id}     Get stage details
PATCH  /api/v1/pipeline_stages/{id}     Update stage
DELETE /api/v1/pipeline_stages/{id}     Delete stage
```

### Quotes

```
POST   /api/v1/quotes                   Create a quote
GET    /api/v1/quotes                   List quotes
GET    /api/v1/quotes/{quote_id}        Get quote details
PATCH  /api/v1/quotes/{quote_id}        Update quote
POST   /api/v1/quotes/{quote_id}/send   Send quote to customer
POST   /api/v1/quotes/{quote_id}/accept Mark as accepted
```

### Bookings

```
POST   /api/v1/bookings                 Create a booking
GET    /api/v1/bookings                 List bookings
GET    /api/v1/bookings/{booking_id}    Get booking details
PATCH  /api/v1/bookings/{booking_id}    Update booking
DELETE /api/v1/bookings/{booking_id}    Delete booking
```

### Invoices

```
POST   /api/v1/invoices                 Create an invoice
GET    /api/v1/invoices                 List invoices (supports `lead_id`)
GET    /api/v1/invoices/{invoice_id}    Get invoice details
```

### Payments

```
POST   /api/v1/payments                 Record a payment
GET    /api/v1/payments                 List payments (supports `invoice_id`)
```

### Tasks

```
POST   /api/v1/tasks                    Create a task
GET    /api/v1/tasks                    List tasks
GET    /api/v1/tasks/{task_id}          Get task details
PATCH  /api/v1/tasks/{task_id}          Update task
DELETE /api/v1/tasks/{task_id}          Delete task
```

### Messages

```
POST   /api/v1/messages                 Send/create a message
GET    /api/v1/messages                 List messages
GET    /api/v1/messages/{message_id}    Get message details
```

### Notifications

```
GET    /api/v1/notifications            List notifications for user
PATCH  /api/v1/notifications/{id}       Mark as read
DELETE /api/v1/notifications/{id}       Delete notification
```

### Attachments

```
POST   /api/v1/attachments/upload       Upload an attachment to R2
GET    /api/v1/attachments              List attachments by `entity_type` and `entity_id`
```

### Health Check

```
GET    /health                          Health check endpoint
```

---

## Services Layer

The services layer contains all business logic. Each service is responsible for:

- Validating input data
- Checking permissions
- Orchestrating database operations
- Handling errors
- Maintaining business rules
- Coordinating multi-step workflows such as invoice numbering, payment status recalculation, and attachment uploads

### Example: Lead Service

The `lead_service.py` demonstrates the typical service pattern:

```python
def create_lead(session: Session, current_user: User, data: LeadCreate) -> Lead:
    """
    Create a new lead with business_id from current user.

    1. Validates that the pipeline stage belongs to user's business
    2. Creates the lead with business_id
    3. Persists to database
    4. Returns created lead
    """
    lead_data = data.model_dump()
    lead_data["business_id"] = current_user.business_id

    # Validate stage belongs to this business
    stage = get_pipeline_stage_by_id(
        session=session,
        business_id=current_user.business_id,
        stage_id=lead_data["stage_id"],
    )
    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage not found for current business",
        )

    lead = Lead(**lead_data)
    return repo_create_lead(session, lead)


def list_leads(session: Session, current_user: User) -> list[Lead]:
    """
    List all leads for the current user's business.

    Returns only leads belonging to the user's business.
    """
    return list_leads_for_business(session, business_id=current_user.business_id)


def move_lead_stage(
    session: Session,
    current_user: User,
    lead_id: UUID,
    new_stage_id: UUID,
) -> Lead:
    """
    Move a lead to a different pipeline stage.

    1. Validates that lead exists and belongs to user's business
    2. Validates that new stage belongs to user's business
    3. Updates the lead's stage_id
    4. Creates a lead activity record for audit trail
    5. Returns updated lead
    """
    lead = get_lead(session, current_user, lead_id)

    if lead.stage_id == new_stage_id:
        return lead  # No change needed

    # Validate new stage
    new_stage = get_pipeline_stage_by_id(
        session=session,
        business_id=current_user.business_id,
        stage_id=new_stage_id,
    )
    if new_stage is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage not found for current business",
        )

    # Move the lead
    updated_lead = repo_move_lead_stage(session, lead, new_stage_id)

    # Record activity for audit trail
    # ... (create activity record)

    return updated_lead
```

### Service Responsibilities

Each service should:

1. **Receive dependencies** via function parameters (session, current_user)
2. **Validate business logic** (permissions, constraints)
3. **Call repositories** for data operations
4. **Handle errors** with appropriate HTTP exceptions
5. **Return domain models** (not schemas)

---

## Repository Layer

The repository layer provides data access abstractions. Each repository:

- Contains database queries
- Handles CRUD operations
- Manages session commits
- Returns SQLModel instances

### Example: Lead Repository

```python
def create_lead(session: Session, lead: Lead) -> Lead:
    """Create and persist a lead."""
    session.add(lead)
    session.commit()
    session.refresh(lead)  # Refresh to get any database defaults
    return lead


def get_lead_by_id(
    session: Session,
    business_id: UUID,
    lead_id: UUID
) -> Lead | None:
    """Fetch a lead by ID (with business isolation)."""
    statement = select(Lead).where(
        Lead.id == lead_id,
        Lead.business_id == business_id
    )
    return session.exec(statement).first()


def list_leads_for_business(
    session: Session,
    business_id: UUID
) -> list[Lead]:
    """List all leads for a business."""
    statement = select(Lead).where(Lead.business_id == business_id)
    return list(session.exec(statement).all())


def move_lead_stage(
    session: Session,
    lead: Lead,
    new_stage_id: UUID
) -> Lead:
    """Move a lead to a new stage."""
    lead.stage_id = new_stage_id
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead
```

### Query Best Practices

1. **Always filter by business_id** for multi-tenancy isolation
2. **Use indexes** on frequently filtered fields
3. **Eager load related data** when needed
4. **Minimize N+1 queries** by joining related tables
5. **Use pagination** for large result sets

---

## Database Design

### Multi-tenancy Model

The system uses **database-level row-level security**:

- Every table has a `business_id` foreign key
- All queries filter by `business_id`
- Users can only access their business's data

### Schema

```sql
-- Core entities
businesses
  - id (UUID, PK)
  - name (string)
  - phone (string)
  - whatsapp_number (string, nullable)
  - owner_user_id (UUID, FK users)
  - created_at (timestamp)

users
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - email (string)
  - phone (string)
  - name (string)
  - role (string: owner|manager|staff)
  - created_at (timestamp)

-- CRM entities
customers
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - name (string)
  - phone (string)
  - email (string, nullable)
  - notes (string, nullable)
  - created_at (timestamp)
  - Indexes: (business_id, phone)

pipelines
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - name (string)
  - created_at (timestamp)
  - Index: business_id

pipeline_stages
  - id (UUID, PK)
  - pipeline_id (UUID, FK pipelines)
  - name (string)
  - position (integer)
  - color (string)
  - Index: pipeline_id

leads
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - customer_id (UUID, FK customers)
  - stage_id (UUID, FK pipeline_stages)
  - title (string)
  - source (string, nullable)
  - event_date (date, nullable)
  - estimated_value (decimal, nullable)
  - assigned_to (UUID, FK users, nullable)
  - notes (string, nullable)
  - created_at (timestamp)
  - updated_at (timestamp)
  - Indexes: (business_id, stage_id), (business_id, created_at), (business_id, customer_id)

lead_activities
  - id (UUID, PK)
  - lead_id (UUID, FK leads)
  - type (string: call|whatsapp|meeting|note|status_change)
  - description (string)
  - created_by (UUID, FK users)
  - created_at (timestamp)
  - Indexes: lead_id, created_by

-- Sales entities
quotes
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - lead_id (UUID, FK leads)
  - quote_number (string)
  - total_amount (decimal)
  - status (string: draft|sent|accepted|rejected)
  - issued_date (date)
  - expires_date (date)
  - created_at (timestamp)
  - Index: business_id

bookings
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - lead_id (UUID, FK leads)
  - booking_date (date)
  - status (string: confirmed|in_progress|completed|cancelled)
  - notes (string, nullable)
  - created_at (timestamp)
  - updated_at (timestamp)
  - Index: business_id

-- Billing entities
invoices
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - booking_id (UUID, FK bookings)
  - invoice_number (string)
  - total_amount (decimal)
  - status (string: draft|sent|paid|partial|overdue)
  - issued_date (date)
  - due_date (date)
  - created_at (timestamp)
  - Indexes: business_id, invoice_number

payments
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - invoice_id (UUID, FK invoices)
  - amount (decimal)
  - method (string: upi|cash|bank_transfer|card)
  - reference (string)
  - created_at (timestamp)
  - Index: business_id

-- Task management
tasks
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - title (string)
  - assigned_to (UUID, FK users)
  - status (string: pending|in_progress|done)
  - due_date (date, nullable)
  - priority (integer)
  - created_at (timestamp)
  - updated_at (timestamp)
  - Index: business_id

-- Communication
messages
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - contact_id (UUID, FK customers)
  - content (string)
  - channel (string: whatsapp|sms|email)
  - direction (string: incoming|outgoing)
  - created_at (timestamp)
  - Index: business_id

notifications
  - id (UUID, PK)
  - user_id (UUID, FK users)
  - title (string)
  - message (string)
  - is_read (boolean)
  - created_at (timestamp)
  - Index: user_id

attachments
  - id (UUID, PK)
  - business_id (UUID, FK businesses)
  - entity_type (string: lead|quote|invoice|task)
  - entity_id (UUID)
  - filename (string)
  - file_url (string)
  - file_size (integer)
  - created_at (timestamp)
  - Index: business_id
```

### Index Strategy

Critical indexes for performance:

- **business_id** on all tables (multi-tenancy filtering)
- **Foreign keys** for relationship joins
- **Composite indexes** for common filter combinations
- Example: `(business_id, stage_id)` for pipeline views

---

## Workflow

### Customer-to-Payment Workflow

This is the primary workflow the system supports:

```
1. CUSTOMER
   └─ Create a customer record

2. LEAD
   └─ Convert customer interest into a lead
   └─ Assign to a pipeline stage (e.g., "Inquiry")
   └─ Track interactions via lead activities

3. QUOTE
   └─ Create and send a quote/proposal
   └─ Customer reviews and responds

4. BOOKING
   └─ Convert accepted quote to a confirmed booking
   └─ Track service schedule
   └─ Update status: Confirmed → In Progress → Completed

5. INVOICE
   └─ Generate invoice from booking
   └─ Track payment status

6. PAYMENT
   └─ Record payment received
   └─ Update invoice status to "Paid"
```

### Lead Lifecycle

```
Lead Created
    ↓
Customer Inquiry (Stage: Inquiry)
    ↓ [Lead Activity: Call/Message/Meeting]
Proposal Sent (Stage: Proposal)
    ↓ [Lead Activity: Quote sent]
Waiting for Response (Stage: Negotiation)
    ↓ [Lead Activity: Follow-up call/message]
Accepted (Stage: Won) → Booking Created
    ↓
Or
    ↓
Rejected (Stage: Lost) → Archive
```

---

## Configuration

### Environment Variables

The system uses `.env` file for configuration:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost/crm_db

# WhatsApp
WHATSAPP_WEBHOOK_VERIFY_TOKEN=crm-whatsapp-webhook-verify-token

# Cloudflare R2
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<r2-access-key>
R2_SECRET_ACCESS_KEY=<r2-secret-key>
R2_BUCKET_NAME=<bucket-name>
R2_PUBLIC_URL=https://<public-bucket-url>
```

### Configuration File

Located in `app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql://crm_user:crm_password@localhost:5432/crm_db"
    whatsapp_webhook_verify_token: str = "crm-whatsapp-webhook-verify-token"
    R2_ENDPOINT_URL: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""
    R2_PUBLIC_URL: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

settings = Settings()
```

---

## Development Guide

### Project Setup

1. **Clone the repository**

   ```bash
   git clone <repo-url>
   cd crm-saas-backend
   ```

2. **Create virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Setup environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your database URL
   ```

5. **Run database migrations**

   ```bash
   alembic upgrade head
   ```

6. **Start the development server**

   ```bash
   uvicorn app.main:app --reload
   ```

   API will be available at `http://localhost:8000`
   - API docs: `http://localhost:8000/api/docs`
   - ReDoc: `http://localhost:8000/api/redoc`

### Using Docker

```bash
# Start with Docker Compose
docker-compose up

# Run migrations inside container
docker-compose exec api alembic upgrade head

# Access the API
# http://localhost:8000
```

### Database Migrations

**Create a new migration:**

```bash
alembic revision --autogenerate -m "Description of changes"
```

**Apply migrations:**

```bash
alembic upgrade head
```

**Rollback:**

```bash
alembic downgrade -1
```

### Adding a New Feature

Example: Add a new "Review" entity

1. **Create the model** in `app/models/review.py`

   ```python
   class Review(ReviewBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
       __tablename__ = "reviews"
       business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
       lead_id: uuid.UUID = Field(foreign_key="leads.id")
       rating: int
   ```

2. **Create the repository** in `app/repositories/review_repository.py`

   ```python
   def create_review(session: Session, review: Review) -> Review:
       session.add(review)
       session.commit()
       session.refresh(review)
       return review
   ```

3. **Create the service** in `app/services/review_service.py`

   ```python
   def create_review(session: Session, current_user: User, data: ReviewCreate) -> Review:
       review_data = data.model_dump()
       review_data["business_id"] = current_user.business_id
       review = Review(**review_data)
       return repo_create_review(session, review)
   ```

4. **Create API routes** in `app/api/v1/reviews.py`

   ```python
   @router.post("/reviews", response_model=ReviewRead)
   def create_review(payload: ReviewCreate, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
       return service_create_review(session, current_user, payload)
   ```

5. **Register the router** in `app/main.py`

   ```python
   from app.api.v1.reviews import router as reviews_router
   app.include_router(reviews_router, prefix="/api/v1", tags=["reviews"])
   ```

6. **Create a database migration**
   ```bash
   alembic revision --autogenerate -m "Add reviews table"
   alembic upgrade head
   ```

### Testing

(Future: Testing framework and guidelines to be added)

### Code Standards

- **PEP 8** compliant code
- **Type hints** for all function parameters and returns
- **Docstrings** for complex functions and classes
- **Error handling** with appropriate HTTP status codes
- **Input validation** using Pydantic models
- **Database isolation** by business_id (multi-tenancy)

---

## Summary

This CRM SaaS backend provides a scalable, well-organized foundation for managing customer relationships and service businesses. The layered architecture ensures maintainability, the multi-tenant design supports multiple businesses, and the core workflow (Customer → Lead → Quote → Booking → Invoice → Payment) aligns with typical service business operations.

For questions or contributions, refer to the existing code patterns and maintain consistency with the established architecture.
