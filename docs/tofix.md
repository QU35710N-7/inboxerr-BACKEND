1.Title: Clarify /tasks/{id} vs real batch tracking
Note: Current endpoint actually returns MessageBatch progress (batch-…) but route name implies background task. Rename or add real task tracker later so status polling for big sends (e.g., 50 k messages) is accurate.





2.CSV import can exhaust memory on large files
await file.read() loads the entire upload into RAM. For big CSVs (> ~50 MB) this will block the event-loop and may kill small instances.



2.


1. Problem Statement
All three “send” endpoints in app/api/v1/endpoints/messages.py accept a BackgroundTasks object but never enqueue tasks with it.

py
Copy
Edit
async def send_message(..., background_tasks: BackgroundTasks, ...)
    ...
    result = await sms_sender.send_message(...)   # ← blocks request
As a result, every request waits for the SMS gateway (or bulk CSV parsing) before returning, contradicting the 202 “accepted / processing” contract and limiting throughput.

2. Impact
Area	Consequence
Latency / UX	Client sits idle for seconds on large batches; 202 is misleading.
Throughput	Long-running awaits tie up the event loop → fewer concurrent requests.
Scalability	Memory spike when importing large CSVs (await file.read() loads whole file in RAM).
Task progress	/tasks/{id} endpoint often returns 404 because no task records are created.
...








important for mvp:


Inboxerr – Campaign Message Personalization & Integration Update
1. Objective Overview
Enforce every campaign includes message content (raw text or template) at creation.

Enable dynamic, per-recipient message personalization (“merge fields”) via CSV import (e.g., {{name}}, {{order_no}}, etc.).

Flow: Create Campaign (w/ message/template) → Attach contacts (with variables) → Start → Generate/send personalized Message records.

2. Schema Changes (Pydantic & DB)
CampaignCreate, CampaignCreateFromCSV:

Add: message_content: Optional[str]

Add: template_id: Optional[str]

Enforce at least one required (via root validator).

CSV Import: Accept extra columns as variables; for each contact, store variables JSON.

Message Model:

Add: variables: JSON column per message for per-recipient substitution.

CampaignResponse:

Return message_content, template_id (whichever used).

DB:

Migrate Campaign and Message tables as above.

3. Endpoint Behavior Updates
POST /api/v1/campaigns

Accepts message_content or template_id (reject if neither).

Stores accordingly.

POST /api/v1/campaigns/from-csv

Accept campaign data (with content or template), plus CSV with extra columns mapped as variables per row.

For each recipient: create Message with correct variables.

POST /api/v1/campaigns/{id}/start

For each Message:

If template_id: render message using stored variables JSON per recipient.

If message_content: use as-is (no variables).

4. Edge Cases & Validations
Both fields provided: template takes precedence or return error.

Only template_id: validate existence, ownership, and activeness.

Only message_content: validate length, reject if empty.

CSV upload: error if required columns (e.g., phone) missing, or variable columns don’t match template variables.

5. Security & Permissions
User can only reference their own template_id.

Only owner can create/edit campaigns/messages.

6. Testing & Docs
Cover:

Campaign creation failure (no content/template).

Success with either.

Multiple message records, correct variable mapping.

Template variable substitution logic.

Update API docs/OpenAPI for new fields and logic.

7. Sample Flow
Campaign create (template_id or content required).

CSV upload:

Phone, name, order_no columns (for example).

Store:

phone: "+15555555555"

variables: { "name": "Bob", "order_no": "12345" }

Start campaign:

For each message, render template with per-recipient variables, send.

Inboxerr – Campaign Message Personalization & Integration Update
1. Objective Overview
Enforce every campaign includes message content (raw text or template) at creation.

Enable dynamic, per-recipient message personalization (“merge fields”) via CSV import (e.g., {{name}}, {{order_no}}, etc.).

Flow: Create Campaign (w/ message/template) → Attach contacts (with variables) → Start → Generate/send personalized Message records.

2. Schema Changes (Pydantic & DB)
CampaignCreate, CampaignCreateFromCSV:

Add: message_content: Optional[str]

Add: template_id: Optional[str]

Enforce at least one required (via root validator).

CSV Import: Accept extra columns as variables; for each contact, store variables JSON.

Message Model:

Add: variables: JSON column per message for per-recipient substitution.

CampaignResponse:

Return message_content, template_id (whichever used).

DB:

Migrate Campaign and Message tables as above.

3. Endpoint Behavior Updates
POST /api/v1/campaigns

Accepts message_content or template_id (reject if neither).

Stores accordingly.

POST /api/v1/campaigns/from-csv

Accept campaign data (with content or template), plus CSV with extra columns mapped as variables per row.

For each recipient: create Message with correct variables.

POST /api/v1/campaigns/{id}/start

For each Message:

If template_id: render message using stored variables JSON per recipient.

If message_content: use as-is (no variables).

4. Edge Cases & Validations
Both fields provided: template takes precedence or return error.

Only template_id: validate existence, ownership, and activeness.

Only message_content: validate length, reject if empty.

CSV upload: error if required columns (e.g., phone) missing, or variable columns don’t match template variables.

5. Security & Permissions
User can only reference their own template_id.

Only owner can create/edit campaigns/messages.

6. Testing & Docs
Cover:

Campaign creation failure (no content/template).

Success with either.

Multiple message records, correct variable mapping.

Template variable substitution logic.

Update API docs/OpenAPI for new fields and logic.

7. Sample Flow
Campaign create (template_id or content required).

CSV upload:

Phone, name, order_no columns (for example).

Store:

phone: "+15555555555"

variables: { "name": "Bob", "order_no": "12345" }

Start campaign:

For each message, render template with per-recipient variables, send.





------------CSSVV-------


CSV → Contacts → Queued Messages — Backend Architecture Specification (FINAL)
(Share this with the backend team; it contains every decision agreed so far, including the library pick.)

1 — Objectives
Goal	Success Metric
MVP CSV upload	100 k-row file parses & queues in < 3 min on a single mid-tier VM
Zero RAM spikes	RSS remains < 300 MB during import
No raw-file retention	Temp file deleted immediately after parse; only SHA-256 stored
Future-proof queue layer	Parsing & dispatch logic unchanged when moving from in-process tasks to Celery/RQ

2 — End-to-End Flow
POST /imports/csv
Streams file to /tmp/{import_id}.csv, computes SHA-256, creates import_jobs row (status=processing), returns 202.

BackgroundTasks schedules process_csv(import_id, path) immediately.

Parser (process_csv)
Reads with csv.DictReader, validates, bulk-inserts Contacts every 1 000 rows, updates counters.

Deletes temp file, updates import_jobs → status=success | error.

Campaign creation accepts import_id; a single INSERT … SELECT pre-creates Message rows (status=queued).

Dispatcher loop (async task in app-lifespan) dequeues 50 queued messages at a time using FOR UPDATE SKIP LOCKED, calls sms_sender, updates status, repeats.

(Later Celery/RQ upgrade: only the runner that invokes process_csv and the dispatcher registration change; the inner logic stays identical.)

3 — Data Model Additions
Table	Key Columns	Notes
import_jobs	id UUID, filename, sha256, `status enum(processing	success
contacts	id UUID, import_id FK, phone, name, tags[], created_at; unique (import_id, phone)	
(optional Phase-2) campaign_batches	id UUID, campaign_id FK, range_start, range_end, status	

messages already exists; add an optional import_id FK for lineage.

4 — API Surface
Method	Path	Description
POST	/api/v1/imports/csv	Multipart CSV upload → {import_id} & 202
GET	/api/v1/imports/{id}	Progress (status, row counts, sha256)
GET	/api/v1/imports/{id}/contacts	Paginated preview (first 100 rows)
POST	/api/v1/campaigns	Existing payload + import_id (alternative to contact list)

5 — Responsibilities & File Map
Component	File (suggested)	Notes
Upload handler	app/api/v1/endpoints/imports.py	Streams, hashes, enqueues parser
Parser	app/services/imports/parser.py	Validates rows, bulk inserts, deletes temp file
ImportJob model	app/models/import_job.py	Enum ImportStatus shared in constants
Dispatcher loop	app/services/sms/dispatcher.py	Async loop, FOR UPDATE SKIP LOCKED, asyncio.Semaphore
Alembic migration	alembic/versions/<ts>_add_import_tables.py	Adds tables & FKs

6 — Library Decision (MVP)
Library	Pros	Cons	Decision
Std-lib csv.DictReader	Zero new deps, true streaming, constant memory, parses 100 k rows ≈ 2 s	Pure-Python (slower for > 1 M rows)	✅ Use now
pandas read_csv(chunksize)	Fast C engine, familiar API	Heavy wheel (~35 MB), higher RAM, unnecessary for current scale	Park for analytics phase
Polars Arrow read_csv(streaming)	Extremely fast, out-of-core	New binary dep, team unfamiliar	Evaluate in Phase-2 (millions of rows)

→ Implement parser with csv.DictReader; swapping to Pandas/Polars later is a one-liner.

7 — Scaling & Switch-over Plan
Stage	Trigger	Action
Phase 0 — BackgroundTasks	Works to ~200 k rows/day on one VM	Monitor p95 latency, CPU
Phase 1 — Celery/RQ	p95 > 5 min or CPU > 75 % sustained	Add Redis/Rabbit, 2-4 worker containers
Phase 2 — Horizontal scale	> 1 M rows/day	Partition messages, adopt campaign_batches, more workers

8 — Security & Compliance
Raw CSV deleted post-parse; only SHA-256 retained.

/tmp mounted with noexec,nosuid; daily cron cleans orphan files.

Future audit need → stream to S3 instead of /tmp (30-day lifecycle), same parser.

9 — Engineering Task List
DB migrations — import_jobs, contacts, add import_id FK to messages.

Add ImportStatus enum in shared constants.

Implement upload endpoint with streaming + SHA-256.

Build parser service with bulk inserts (1 000 rows/commit) and file deletion.

Extend campaign creation to accept import_id and pre-create queued messages.

Implement dispatcher loop with FOR UPDATE SKIP LOCKED, asyncio.Semaphore.

Unit & load tests:

100 k-row synthetic CSV completes < 3 min.

Dispatcher sends 10 k messages, no duplicates/deadlocks.

Add Prometheus counters: import_rows_processed_total, messages_sent_total, dispatcher_loop_duration_seconds.

