CSV import can exhaust memory on large files
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


