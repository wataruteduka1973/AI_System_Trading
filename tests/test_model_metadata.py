from app.db.session import Base
from app.models import AppUser, AuditLog, BackfillJob, MarketDataGap
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import configure_mappers


def test_all_modeled_foreign_key_targets_are_registered() -> None:
    configure_mappers()

    assert AppUser.__table__ is Base.metadata.tables["fx.app_user"]
    assert MarketDataGap.__table__ is Base.metadata.tables["fx.market_data_gap"]
    assert AuditLog.__table__.c.actor_id.foreign_keys
    assert BackfillJob.__table__.c.gap_id.foreign_keys
    assert isinstance(AuditLog.__table__.c.ip_address.type, INET)
