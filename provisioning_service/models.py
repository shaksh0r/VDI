from datetime import date
from typing import Optional

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

# Define the base class for declarative models
class Base(DeclarativeBase):
    pass

class Temp(Base):
    __tablename__ = 'temp'

    # Identity(always=True) translates to GENERATED ALWAYS AS IDENTITY
    id = Column(Integer,primary_key=True,autoincrement=True)
    create_time = Column(DateTime, server_default=func.now())
    name = Column(String(50),nullable=False,unique=True)

    def __repr__(self) -> str:
        return f"<Temp(id={self.id}, create_time={self.create_time}, name='{self.name}')>"