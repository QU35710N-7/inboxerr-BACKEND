Based on my comprehensive analysis of the Inboxerr backend codebase, I'll now provide the deep diagnostic analysis.
Phase 1 — System Comprehension
Executive Summary

CSV Import Memory Exhaustion: await file.read() in /messages/import and /campaigns/from-csv loads entire files into RAM, causing OOM on >50MB files (app/api/v1/endpoints/messages.py:242, app/api/v1/endpoints/campaigns.py:71)
Blocking Message Endpoints: All message send endpoints (send_message, send_batch, import_messages) accept BackgroundTasks but immediately await SMS operations, violating 202 "accepted" contract and tying up event loop
Missing Campaign Personalization Schema: Campaign and Message models lack required message_content, template_id, and variables JSON columns specified in personalization requirements
Incomplete CSV→Contacts→Queued Messages Pipeline: Missing import_jobs, contacts tables and streaming parser service described in architecture specification
Synchronous SMS Gateway Calls: Direct awaits in sms_sender.send_message() block request threads instead of queuing for background processing
Task Status Tracking Gaps: /tasks/{id} endpoint returns 404 because no task records are created for background operations
Missing Database Migrations: Required schema changes for personalization (variables JSON, message_content, template_id) have no corresponding Alembic migrations
Event Loop Bottlenecks: CSV parsing and SMS sending operations perform synchronous I/O that should be moved to background workers
Unguarded Memory Usage: No streaming mechanisms or memory limits for file processing operations
Missing Queue Infrastructure: Architecture calls for dispatcher loop with FOR UPDATE SKIP LOCKED but implementation missing

Detailed Gap Analysis
ComponentIssueSeverityFile(s)Line #CSV Importawait file.read() loads entire file in RAMCriticalapp/api/v1/endpoints/messages.py242CSV Importawait file.read() in campaign CSV uploadCriticalapp/api/v1/endpoints/campaigns.py71Message Endpointssend_message awaits SMS instead of backgroundingHighapp/api/v1/endpoints/messages.py45-52Message Endpointssend_batch awaits SMS instead of backgroundingHighapp/api/v1/endpoints/messages.py82-89Message Endpointsimport_messages awaits processing instead of backgroundingHighapp/api/v1/endpoints/messages.py242-270Campaign ModelMissing message_content columnHighapp/models/campaign.pyN/ACampaign ModelMissing template_id columnHighapp/models/campaign.pyN/AMessage ModelMissing variables JSON columnHighapp/models/message.pyN/AMessage ModelMissing import_id FK columnMediumapp/models/message.pyN/ADatabase SchemaMissing import_jobs tableHighN/AN/ADatabase SchemaMissing contacts tableHighN/AN/ACSV PipelineMissing streaming parser serviceHighapp/services/imports/parser.pyMissing fileCSV PipelineMissing upload endpoint with streamingHighapp/api/v1/endpoints/imports.pyMissing fileSMS QueueMissing dispatcher loop implementationMediumapp/services/sms/dispatcher.pyMissing filePydantic SchemasCampaign schemas missing message_content/template_id validationMediumapp/schemas/campaign.pyN/ABackground TasksBackgroundTasks parameters never usedMediumMultiple endpoint filesMultipleTask TrackingNo task record creation for /tasks/{id} endpointMediumMessage endpointsN/A
Call Graph & Data Flow Analysis
Current CSV Import Flow:

POST /messages/import → import_messages()
await file.read() → Blocks event loop, loads full file
csv.reader() → Synchronous parsing
sms_sender.schedule_batch_from_numbers() → Direct await
sms_sender.send_batch() → Blocks until SMS gateway responds

Current Message Send Flow:

POST /messages/send → send_message()
sms_sender.send_message() → Direct await
_send_to_gateway() → HTTP call blocks request
Returns 202 but client already waited for SMS completion

Required vs Actual Database Schema:

Missing: import_jobs table with processing status tracking
Missing: contacts table for normalized contact storage
Missing: variables JSON column on message table
Missing: message_content, template_id columns on campaign table
Missing: import_id FK on message table for lineage

Memory Usage Hotspots
Line-by-Line Critical Sections:

app/api/v1/endpoints/messages.py:242 - contents = await file.read()
app/api/v1/endpoints/campaigns.py:71 - contents = await file.read()
No streaming mechanisms anywhere in CSV processing pipeline
No memory limits or file size validation beyond basic checks

Step-by-Step Remediation Roadmap
Phase 1A: Critical Memory & Blocking Fixes (Week 1)
Step 1.1: Add Streaming CSV Upload

Files: Create app/api/v1/endpoints/imports.py
Changes: Stream file to /tmp/{uuid}.csv, compute SHA-256, return 202
Tests: Upload 100MB file, verify constant memory usage
Outcome: Memory usage ≤ 50MB for any file size

Step 1.2: Create Background Task Infrastructure

Files: Modify app/api/v1/endpoints/messages.py lines 45-52, 82-89, 242-270
Changes: Use background_tasks.add_task() instead of direct awaits
Tests: Verify 202 returns immediately, work completes in background
Outcome: p95 endpoint latency ≤ 250ms

Phase 1B: Database Schema Updates (Week 1-2)
Step 1.3: Add Required Migrations

Files: Create alembic/versions/{timestamp}_add_personalization_columns.py
Changes: Add message_content, template_id to campaigns; variables JSON to messages
Tests: Run migration, verify schema matches spec
Outcome: All personalization spec columns available

Step 1.4: Create Import Tables

Files: Create alembic/versions/{timestamp}_add_import_tables.py
Changes: Add import_jobs, contacts tables per architecture spec
Tests: Create import job, verify foreign keys work
Outcome: CSV import pipeline data models ready

Phase 2A: Streaming Parser Service (Week 2-3)
Step 2.1: Implement CSV Parser

Files: Create app/services/imports/parser.py
Changes: Use csv.DictReader with 1000-row bulk commits
Tests: Parse 100k-row file in <3 minutes
Outcome: Constant memory parsing of large CSVs

Step 2.2: Connect Import Pipeline

Files: Modify app/api/v1/endpoints/campaigns.py, create import endpoints
Changes: Accept import_id in campaign creation, link to contacts
Tests: CSV → contacts → campaign → messages flow
Outcome: End-to-end personalized campaign creation

Phase 2B: Queue Infrastructure (Week 3-4)
Step 2.3: Background Message Dispatcher

Files: Create app/services/sms/dispatcher.py
Changes: Async loop with FOR UPDATE SKIP LOCKED, asyncio.Semaphore
Tests: Process 10k queued messages without deadlocks
Outcome: Scalable message processing queue

Step 2.4: Update Pydantic Schemas

Files: Modify app/schemas/campaign.py
Changes: Add message_content/template_id validation, require one
Tests: Verify validation errors when both missing
Outcome: API contracts match personalization spec

Migration Dependencies

Schema Migrations: Must run before code deployment
Import Tables: Required before streaming parser
Background Tasks: Can be deployed incrementally per endpoint
Queue Infrastructure: Requires message status updates to work

Measurable Success Criteria

Memory: CSV import RAM usage ≤ 300MB for 100k-row files
Latency: Message endpoints p95 ≤ 250ms (currently seconds)
Throughput: Support 1000+ concurrent CSV uploads
Reliability: Zero OOM errors on production file sizes
Functionality: Campaign personalization with variables working end-to-end



patterns:

ImportError: Inherits from InboxerrException
Migration naming: {hash}_add_campaign_personalization_columns.py and {hash}_add_import_tables.py
Background tasks: Async-only interface compatible with existing async patterns
File validation: Both extension (.csv) AND MIME type (text/csv, text/plain)
Row count validation: During streaming (abort at 1M+ rows)
Error storage: JSONB column with {row, column, message} objects
Status enum: PROCESSING, SUCCESS, FAILED, CANCELLED
Template validation: Check ownership + active status at creation
Concurrent limits: Enforce at upload (return 429 if 5+ active jobs)
Cleanup: Delete temp files immediately, 4-day retention for job records