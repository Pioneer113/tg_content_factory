from __future__ import annotations

import pytest

from tg_agent.database.repositories.accounts import AccountsRepository
from tg_agent.database.repositories.channel_stats import ChannelStatsRepository
from tg_agent.database.repositories.channels import ChannelsRepository
from tg_agent.database.repositories.collection_tasks import CollectionTasksRepository
from tg_agent.database.repositories.content_pipelines import ContentPipelinesRepository
from tg_agent.database.repositories.filters import FilterRepository
from tg_agent.database.repositories.messages import MessagesRepository
from tg_agent.database.repositories.notification_bots import NotificationBotsRepository
from tg_agent.database.repositories.search_log import SearchLogRepository
from tg_agent.database.repositories.search_queries import SearchQueriesRepository
from tg_agent.database.repositories.settings import SettingsRepository
from tg_agent.models import Channel
from tg_agent.security.session_cipher import SessionCipher


@pytest.fixture
async def accounts_repo(db):
    return AccountsRepository(
        db.db,
        session_cipher=SessionCipher("test-session-encryption-key"),
        database=db,
    )


@pytest.fixture
async def channel_stats_repo(db):
    return ChannelStatsRepository(db.db, database=db)


@pytest.fixture
async def channels_repo(db):
    return ChannelsRepository(db.db, database=db)


@pytest.fixture
async def collection_tasks_repo(db):
    return CollectionTasksRepository(db.db, database=db)


@pytest.fixture
async def content_pipelines_repo(db):
    await db.add_channel(Channel(channel_id=1001, title="Source A"))
    await db.add_channel(Channel(channel_id=1002, title="Source B"))
    return ContentPipelinesRepository(db.db, database=db)


@pytest.fixture
async def filters_repo(db):
    return FilterRepository(db.db, database=db)


@pytest.fixture
async def messages_repo(db):
    return MessagesRepository(db.db, database=db)


@pytest.fixture
async def notification_bots_repo(db):
    return NotificationBotsRepository(db.db, database=db)


@pytest.fixture
async def search_log_repo(db):
    return SearchLogRepository(db.db, database=db)


@pytest.fixture
async def search_queries_repo(db):
    return SearchQueriesRepository(db.db, database=db)


@pytest.fixture
async def settings_repo(db):
    return SettingsRepository(db.db, database=db)
