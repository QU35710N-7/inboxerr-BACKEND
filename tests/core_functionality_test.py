import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock

from app.schemas.message import MessageStatus
from app.db.repositories.messages import MessageRepository
from app.db.repositories.templates import TemplateRepository
from app.db.repositories.campaigns import CampaignRepository
from app.services.event_bus.bus import get_event_bus
from app.services.sms.sender import SMSSender
from app.utils.phone import validate_phone


@pytest.fixture
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


import pytest_asyncio

@pytest_asyncio.fixture
async def event_bus():
    bus = get_event_bus()
    await bus.initialize()
    yield bus
    await bus.shutdown()


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    # Mock session.begin for async context
    session.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=None)))
    return session


@pytest.fixture
def message_repository(mock_session):
    return MessageRepository(mock_session)


@pytest.fixture
def template_repository(mock_session):
    return TemplateRepository(mock_session)


@pytest.fixture
def campaign_repository(mock_session):
    return CampaignRepository(mock_session)


@pytest.fixture
def sms_sender(message_repository, event_bus):
    return SMSSender(message_repository, event_bus)


def test_phone_validation_valid_numbers():
    valid_numbers = ["+12025550108", "+447911123456", "+61412345678", "+33612345678"]
    for number in valid_numbers:
        is_valid, formatted, error, _ = validate_phone(number)
        assert is_valid
        assert formatted.startswith("+")


def test_phone_validation_invalid_numbers():
    invalid_numbers = ["not a number", "123", "+1234567890123456789", "+123abcd5678"]
    for number in invalid_numbers:
        is_valid, *_ = validate_phone(number)
        assert not is_valid


@pytest.mark.asyncio
async def test_create_message(message_repository):
    message_repository.session.refresh.side_effect = lambda x: x
    mock_campaign = MagicMock()
    mock_campaign.total_messages = 0
    with patch("app.db.repositories.campaigns.CampaignRepository.get_by_id", new_callable=AsyncMock, return_value=mock_campaign):
        message = await message_repository.create_message(
            phone_number="+12025550108",
            message_text="Test message",
            user_id="user-123",
            custom_id="custom-123",
            campaign_id="campaign-123"
        )
        assert message.phone_number == "+12025550108"
        assert message.status == MessageStatus.PENDING
        assert message_repository.session.add.call_count >= 1


@pytest.mark.asyncio
async def test_update_message_status(message_repository):
    message = MagicMock()
    message.id = "msg-123"
    message_repository.get_by_id = AsyncMock(return_value=message)
    message_repository.session.refresh.side_effect = lambda x: x
    updated_message = await message_repository.update_message_status(
        message_id="msg-123",
        status=MessageStatus.SENT,
        event_type="test",
        gateway_message_id="gw-123"
    )
    assert updated_message is not None
    message_repository.get_by_id.assert_awaited_with("msg-123")
    assert message_repository.session.add.call_count >= 1


@pytest.mark.asyncio
async def test_apply_template(template_repository):
    template = MagicMock()
    template.content = "Hello {{name}}, your code is {{code}}"
    template_repository.get_by_id = AsyncMock(return_value=template)
    result = await template_repository.apply_template(
        template_id="template-123",
        variables={"name": "John", "code": "123456"}
    )
    assert result == "Hello John, your code is 123456"
    template_repository.get_by_id.assert_awaited_with("template-123")


@pytest.mark.asyncio
async def test_create_template(template_repository):
    template_repository.session.refresh.side_effect = lambda x: x
    template = await template_repository.create_template(
        name="Test Template",
        content="Hello {{name}}",
        description="Test description",
        user_id="user-123"
    )
    assert template.name == "Test Template"
    template_repository.session.add.assert_called_with(template)
    template_repository.session.commit.assert_called()


@pytest.mark.asyncio
async def test_create_campaign(campaign_repository):
    campaign_repository.session.refresh.side_effect = lambda x: x
    campaign = await campaign_repository.create_campaign(
        name="Test Campaign",
        description="Test description",
        user_id="user-123",
        scheduled_start_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert campaign.name == "Test Campaign"
    campaign_repository.session.add.assert_called_with(campaign)
    campaign_repository.session.commit.assert_called()


@pytest.mark.asyncio
async def test_update_campaign_status(campaign_repository):
    campaign = MagicMock()
    campaign.id = "campaign-123"
    campaign.status = "draft"
    campaign_repository.get_by_id = AsyncMock(return_value=campaign)
    with patch("app.services.event_bus.bus.get_event_bus") as mock_get_bus:
        mock_bus = AsyncMock()
        mock_get_bus.return_value = mock_bus
        updated_campaign = await campaign_repository.update_campaign_status(
            campaign_id="campaign-123",
            status="active"
        )
        assert updated_campaign is not None
        assert campaign.status == "active"
        campaign_repository.session.add.assert_called_with(campaign)
        assert mock_bus.publish.called


@pytest.mark.asyncio
async def test_event_bus_subscribe_publish(event_bus):
    callback = AsyncMock()
    subscriber_id = await event_bus.subscribe("test_event", callback)
    assert event_bus.get_subscriber_count("test_event") == 1
    test_data = {"test": "data"}
    success = await event_bus.publish("test_event", test_data)
    assert success
    callback.assert_called_once()
    call_args = callback.call_args[0][0]
    assert call_args["test"] == "data"
    assert call_args["event_type"] == "test_event"
    assert "timestamp" in call_args
    assert "event_id" in call_args
    await event_bus.unsubscribe("test_event", subscriber_id)
    assert event_bus.get_subscriber_count("test_event") == 0


@pytest.mark.asyncio
async def test_sms_sender_send_message(sms_sender):
    sms_sender._send_to_gateway = AsyncMock(return_value={
        "status": MessageStatus.SENT,
        "gateway_message_id": "gw-123"
    })

    mock_message = MagicMock()
    mock_message.id = "msg-123"
    mock_message.dict = MagicMock(return_value={"id": "msg-123"})

    sms_sender.message_repository.create_message = AsyncMock(return_value=mock_message)
    sms_sender.message_repository.update_message_status = AsyncMock(return_value=mock_message)
    sms_sender.message_repository.get_by_id = AsyncMock(return_value=mock_message)  # ✅ add this line

    result = await sms_sender.send_message(
        phone_number="+12025550108",
        message_text="Test message",
        user_id="user-123"
    )

    assert result == {"id": "msg-123"}
    assert sms_sender.message_repository.create_message.called
    assert sms_sender._send_to_gateway.called
    assert sms_sender.message_repository.update_message_status.called
