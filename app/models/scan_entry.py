from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScanEntry(Base):
    __tablename__ = "scan_entries"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, index=True)
    code:        Mapped[str]           = mapped_column(String(100), nullable=False, index=True)
    status:      Mapped[str]           = mapped_column(String(20), nullable=False)
    qr_card_id:  Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("qr_cards.id"), nullable=True)
    scanned_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
