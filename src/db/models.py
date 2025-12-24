from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime
from src.config import settings

Base = declarative_base()

class UserState(Base):
    __tablename__ = "user_states"

    user_id = Column(Integer, primary_key=True)
    conversation_state = Column(JSON, default=dict) # For LangGraph checkpoint
    selected_folder_id = Column(String, nullable=True)
    last_event_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.DB_PATH}")
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

