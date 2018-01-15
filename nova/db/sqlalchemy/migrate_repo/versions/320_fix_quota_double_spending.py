from sqlalchemy import MetaData, Table
from migrate.changeset.constraint import UniqueConstraint

def _build_constraint(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine
    table = Table('quota_usages', meta, autoload=True)
    return UniqueConstraint(
        'project_id', 'user_id', 'resource', 'deleted',
        table=table,
    )

def upgrade(migrate_engine):
    cons = _build_constraint(migrate_engine)
    cons.create()

def downgrade(migrate_engine):
    cons = _build_constraint(migrate_engine)
    cons.drop()
