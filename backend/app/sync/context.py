from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.salesdoctor import SalesDoctorClient


@dataclass
class SyncContext:
    sessionmaker: async_sessionmaker[AsyncSession]
    sd: SalesDoctorClient
    redis: Redis
    settings: Settings
