from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, JSON, DateTime, Text, select
from datetime import datetime
from typing import List, Optional
from src.config import settings

Base = declarative_base()


class BotEvent(Base):
    """Events created through the bot (calendar + Drive folder + sheet)."""
    __tablename__ = "bot_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(Integer, nullable=True, index=True)
    title = Column(String(500), nullable=False)
    start_time = Column(String(100), nullable=False)
    end_time = Column(String(100), nullable=False)
    location = Column(String(500), default="")
    description = Column(Text, default="")
    people_count = Column(String(50), nullable=True)
    email = Column(String(255), default="")
    equipment = Column(Text, default="")
    folder_id = Column(String(255), nullable=True)
    folder_name = Column(String(500), nullable=True)
    calendar_event_id = Column(String(255), nullable=True)
    calendar_link = Column(String(1000), default="")
    cocktails = Column(JSON, default=list)  # ["Margarita", "Mojito", ...]
    created_at = Column(DateTime, default=datetime.utcnow)


class UserState(Base):
    __tablename__ = "user_states"

    user_id = Column(Integer, primary_key=True)
    conversation_state = Column(JSON, default=dict)  # For LangGraph checkpoint
    selected_folder_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Create async engine for PostgreSQL
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Set to True for SQL query logging
    pool_pre_ping=True,  # Verify connections before using
    pool_size=5,  # Connection pool size
    max_overflow=10
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_user_state(user_id: int):
    async with AsyncSessionLocal() as session:
        user = await session.get(UserState, user_id)
        if not user:
            user = UserState(user_id=user_id)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

async def update_user_state(user_id: int, **kwargs):
    async with AsyncSessionLocal() as session:
        user = await session.get(UserState, user_id)
        if user:
            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            await session.commit()


async def create_bot_event(
    *,
    telegram_user_id: Optional[int] = None,
    title: str,
    start_time: str,
    end_time: str,
    location: str = "",
    description: str = "",
    people_count: Optional[str] = None,
    email: str = "",
    equipment: str = "",
    folder_id: Optional[str] = None,
    folder_name: Optional[str] = None,
    calendar_event_id: Optional[str] = None,
    calendar_link: str = "",
    cocktails: Optional[list] = None,
) -> BotEvent:
    async with AsyncSessionLocal() as session:
        ev = BotEvent(
            telegram_user_id=telegram_user_id,
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location or "",
            description=description or "",
            people_count=people_count,
            email=email or "",
            equipment=equipment or "",
            folder_id=folder_id,
            folder_name=folder_name,
            calendar_event_id=calendar_event_id,
            calendar_link=calendar_link or "",
            cocktails=cocktails or [],
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)
        return ev


async def list_bot_events(telegram_user_id: int, limit: int = 50) -> List[BotEvent]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(BotEvent)
            .where(BotEvent.telegram_user_id == telegram_user_id)
            .order_by(BotEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

