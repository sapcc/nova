from sqlalchemy import MetaData, Table, Index


def _build_constraint(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine
    table = Table('block_device_mapping', meta, autoload=True)

    return Index('block_device_mapping_instance_uuid_volume_id_deleted_idx',
                 table.c.instance_uuid, table.c.volume_id, table.c.deleted, unique=True), \
           Index('block_device_mapping_instance_uuid_volume_id_idx',
                 table.c.instance_uuid, table.c.volume_id)


def upgrade(migrate_engine):
    new_index, old_index = _build_constraint(migrate_engine)
    new_index.create(migrate_engine)
    old_index.drop(migrate_engine)


def downgrade(migrate_engine):
    new_index, old_index = _build_constraint(migrate_engine)
    new_index.drop(migrate_engine)
    old_index.create(migrate_engine)
