# Inboxerr Database Schema Overview

This document outlines the structure of the PostgreSQL database used by the Inboxerr backend service. It includes all tables, relationships, and index/foreign key mappings, aligned with the SQLAlchemy models defined in the codebase.

---

## 🔢 Tables & Relationships

| **Table**           | **Primary Keys** | **Foreign Keys**                             | **Relationships**                             |
|---------------------|------------------|-----------------------------------------------|------------------------------------------------|
| `user`              | `id`             | –                                             | Referenced by many tables                      |
| `apikey`            | `id`             | `user_id` → `user(id)`                     | Each API key belongs to a user                 |
| `campaign`          | `id`             | `user_id` → `user(id)`                     | One user owns many campaigns                   |
| `message`           | `id`             | `user_id` → `user(id)`<br>`campaign_id` → `campaign(id)`<br>`batch_id` → `messagebatch(id)` | Messages belong to a campaign and batch        |
| `messagebatch`      | `id`             | `user_id` → `user(id)`                     | Groups messages sent together                  |
| `messageevent`      | `id`             | `message_id` → `message(id)`              | Tracks status updates for a message            |
| `messagetemplate`   | `id`             | `user_id` → `user(id)`                     | Message content templates                      |
| `webhook`           | `id`             | `user_id` → `user(id)`                     | Defines external callbacks                     |
| `webhookdelivery`   | `id`             | `webhook_id` → `webhook(id)`<br>`message_id` → `message(id)` | Stores actual webhook attempts                 |
| `webhookevent`      | `id`             | –                                             | Events that can trigger webhooks               |
| `alembic_version`   | –                | –                                             | Managed by Alembic for schema migrations       |

---

## 📊 Indexes & Performance

Each table includes relevant indexes, such as:
- `id` (primary key, indexed by default)
- Frequently queried fields like `user_id`, `campaign_id`, `status`, and `scheduled_at`

---

## 🔒 Data Types Overview

| **Field**            | **Type**                       |
|----------------------|---------------------------------|
| `id`                 | `character varying` (UUIDs)     |
| `user_id`            | `character varying` (FK)        |
| `campaign_id`        | `character varying` (FK)        |
| `message`            | `text`                          |
| `status`             | `character varying`             |
| `scheduled_at`       | `timestamp without time zone`   |
| `settings`, `data`   | `json`                          |

---

## 📄 How to Inspect the Schema

From inside `psql`:
```bash
\c inboxerr        -- Connect to DB
\dt                 -- List tables
\d tablename       -- Describe table structure
SELECT * FROM tablename LIMIT 5;  -- Preview data
```

To see all foreign keys:
```sql
SELECT conname AS constraint_name, conrelid::regclass AS table,
       a.attname AS column, confrelid::regclass AS referenced_table
FROM pg_constraint
JOIN pg_class ON conrelid = pg_class.oid
JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = ANY(conkey)
WHERE contype = 'f';
```
