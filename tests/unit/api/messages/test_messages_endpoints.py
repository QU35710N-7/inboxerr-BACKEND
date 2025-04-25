import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_send_valid_batch(async_client: AsyncClient, override_auth):
    payload = {
        "messages": [
            {"phone_number": "+1234567890", "message": "Batch Msg 1"},
            {"phone_number": "+1987654321", "message": "Batch Msg 2"}
        ],
        "options": {}
    }
    response = await async_client.post("/api/v1/messages/batch", json=payload)
    assert response.status_code == 202
    assert "results" in response.json()

@pytest.mark.asyncio
async def test_send_empty_batch(async_client: AsyncClient, override_auth):
    payload = {
        "messages": [],
        "options": {}
    }
    response = await async_client.post("/api/v1/messages/batch", json=payload)
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_batch_with_invalid_phone(async_client: AsyncClient, override_auth):
    payload = {
        "messages": [
            {"phone_number": "invalid", "message": "Failing message"}
        ],
        "options": {}
    }
    response = await async_client.post("/api/v1/messages/batch", json=payload)
    assert response.status_code in [422, 502]


@pytest.mark.asyncio
async def test_delete_existing_message(async_client: AsyncClient, override_auth):
    message_id = "existing-msg-id"
    response = await async_client.delete(f"/api/v1/messages/{message_id}")
    assert response.status_code == 204

@pytest.mark.asyncio
async def test_delete_nonexistent_message(async_client: AsyncClient, override_auth):
    message_id = "nonexistent-msg-id"
    response = await async_client.delete(f"/api/v1/messages/{message_id}")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_get_existing_message(async_client: AsyncClient, override_auth):
    message_id = "existing-msg-id"
    response = await async_client.get(f"/api/v1/messages/{message_id}")
    assert response.status_code == 200
    assert response.json()["id"] == message_id

@pytest.mark.asyncio
async def test_get_nonexistent_message(async_client: AsyncClient, override_auth):
    message_id = "nonexistent-msg-id"
    response = await async_client.get(f"/api/v1/messages/{message_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_import_valid_csv(async_client: AsyncClient, override_auth):
    csv_content = "phone\n+1234567890\n+1987654321"
    file = {"file": ("contacts.csv", csv_content, "text/csv")}
    params = {
        "message_template": "Test message",
        "delimiter": ",",
        "has_header": "true",
        "phone_column": "phone"
    }
    response = await async_client.post("/api/v1/messages/import", params=params, files=file)
    assert response.status_code == 202
    assert "task_id" in response.json()

@pytest.mark.asyncio
async def test_import_missing_column(async_client: AsyncClient, override_auth):
    csv_content = "name\nAlice\nBob"
    file = {"file": ("contacts.csv", csv_content, "text/csv")}
    params = {
        "message_template": "Hi",
        "delimiter": ",",
        "has_header": "true",
        "phone_column": "phone"
    }
    response = await async_client.post("/api/v1/messages/import", params=params, files=file)
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_import_invalid_format(async_client: AsyncClient, override_auth):
    csv_content = "random|data|columns"
    file = {"file": ("contacts.csv", csv_content, "text/csv")}
    params = {
        "message_template": "Hi again",
        "delimiter": ",",
        "has_header": "true",
        "phone_column": "phone"
    }
    response = await async_client.post("/api/v1/messages/import", params=params, files=file)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_all_messages(async_client: AsyncClient, override_auth):
    response = await async_client.get("/api/v1/messages/")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert "page_info" in data
    assert isinstance(data["page_info"], dict)

@pytest.mark.asyncio
async def test_filter_messages_by_status(async_client: AsyncClient, override_auth):
    response = await async_client.get("/api/v1/messages/?status=sent")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)

@pytest.mark.asyncio
async def test_filter_messages_by_phone(async_client: AsyncClient, override_auth):
    response = await async_client.get("/api/v1/messages/?phone_number=+1234567890")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)

@pytest.mark.asyncio
async def test_filter_messages_invalid_date(async_client: AsyncClient, override_auth):
    response = await async_client.get("/api/v1/messages/?from_date=invalid-date")
    assert response.status_code in [422, 500]

@pytest.mark.asyncio
async def test_send_valid_message(async_client: AsyncClient, override_auth):
    payload = {
        "phone_number": "+1234567890",
        "message": "Hello there!",
        "scheduled_at": None,
        "custom_id": "test-msg-123"
    }
    response = await async_client.post("/api/v1/messages/send", json=payload)
    assert response.status_code == 202
    assert "id" in response.json()

@pytest.mark.asyncio
async def test_send_invalid_phone(async_client: AsyncClient, override_auth):
    payload = {
        "phone_number": "invalid-phone",
        "message": "Hello there!",
        "scheduled_at": None,
        "custom_id": "test-msg-456"
    }
    response = await async_client.post("/api/v1/messages/send", json=payload)
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_send_empty_message(async_client: AsyncClient, override_auth):
    payload = {
        "phone_number": "+1234567890",
        "message": "",
        "scheduled_at": None,
        "custom_id": "test-msg-789"
    }
    response = await async_client.post("/api/v1/messages/send", json=payload)
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_get_existing_task_status(async_client: AsyncClient, override_auth):
    task_id = "existing-task-id"
    response = await async_client.get(f"/api/v1/messages/tasks/{task_id}")
    assert response.status_code == 200
    assert "status" in response.json()

@pytest.mark.asyncio
async def test_get_nonexistent_task_status(async_client: AsyncClient, override_auth):
    task_id = "nonexistent-task-id"
    response = await async_client.get(f"/api/v1/messages/tasks/{task_id}")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_valid_message_status(async_client: AsyncClient, override_auth):
    message_id = "existing-msg-id"
    payload = {
        "status": "delivered",
        "reason": "confirmed by carrier"
    }
    response = await async_client.put(f"/api/v1/messages/{message_id}/status", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "delivered"

@pytest.mark.asyncio
async def test_update_status_message_not_found(async_client: AsyncClient, override_auth):
    message_id = "nonexistent-msg-id"
    payload = {
        "status": "failed",
        "reason": "user unreachable"
    }
    response = await async_client.put(f"/api/v1/messages/{message_id}/status", json=payload)
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_status_invalid_value(async_client: AsyncClient, override_auth):
    message_id = "existing-msg-id"
    payload = {
        "status": "not_a_valid_status",
        "reason": "some reason"
    }
    response = await async_client.put(f"/api/v1/messages/{message_id}/status", json=payload)
    assert response.status_code == 422
