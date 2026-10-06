"""Persistence-only viewer-session constraints and migration parity."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from app.models.base import generate_uuidv7
from app.models.streaming import StreamSession, StreamViewerSession


TABLE_NAME = "stream_viewer_sessions"
JOINED_AT = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
LEASE_EXPIRES_AT = JOINED_AT + timedelta(seconds=60)


def sqlite_uuid_columns(table):
    # Match the streaming test harness: SQLite needs text rather than native UUID affinity.
    for column in table.columns:
        if isinstance(column.type, postgresql.UUID):
            column.info["postgresql_type"] = column.type
            column.type = sa.Uuid(native_uuid=False)


class MigrationOperations:
    """Execute the revision's table/index operations on an isolated SQLite connection."""

    def __init__(self, connection, metadata):
        self.connection = connection
        self.metadata = metadata

    def create_table(self, name, *items):
        table = sa.Table(name, self.metadata, *items)
        sqlite_uuid_columns(table)
        table.create(self.connection)
        return table

    def create_index(self, name, table_name, columns, **kwargs):
        table = self.metadata.tables[table_name]
        sa.Index(name, *(table.c[column] for column in columns), **kwargs).create(
            self.connection
        )

    def drop_index(self, name, table_name):
        table = self.metadata.tables[table_name]
        index = next(index for index in table.indexes if index.name == name)
        index.drop(self.connection)
        table.indexes.remove(index)

    def drop_table(self, name):
        table = self.metadata.tables[name]
        table.drop(self.connection)
        self.metadata.remove(table)


def load_migration(monkeypatch, operations):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "202_stream_viewer_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("stream_viewer_sessions_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Avoid the local backend/alembic package shadowing the installed Alembic package.
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "alembic", SimpleNamespace(op=operations))
        spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["model", "migration"])
def schema(request, monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True))
    sa.Table(
        "stream_sessions", metadata,
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            metadata.create_all(connection)
            migration = None
            if request.param == "model":
                table = StreamViewerSession.__table__.to_metadata(metadata)
                sqlite_uuid_columns(table)
                table.create(connection)
            else:
                migration = load_migration(
                    monkeypatch, MigrationOperations(connection, metadata)
                )
                migration.upgrade()
                table = metadata.tables[TABLE_NAME]
            stream_id, user_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(metadata.tables["stream_sessions"].insert(), {"id": stream_id})
            connection.execute(metadata.tables["users"].insert(), {"id": user_id})
            connection.commit()
            yield SimpleNamespace(
                connection=connection, metadata=metadata, table=table,
                migration=migration, stream_id=stream_id, user_id=user_id,
            )
    finally:
        engine.dispose()


def viewer_row(schema, **overrides):
    return {
        "id": generate_uuidv7(),
        "stream_session_id": schema.stream_id,
        "user_id": schema.user_id,
        "client_session_id": uuid.uuid4(),
        "joined_at": JOINED_AT,
        "lease_expires_at": LEASE_EXPIRES_AT,
        **overrides,
    }


def insert_row(schema, **overrides):
    row = viewer_row(schema, **overrides)
    schema.connection.execute(schema.table.insert(), row)
    schema.connection.commit()
    return row


def test_model_shape_and_relationship():
    table = StreamViewerSession.__table__
    assert set(table.c.keys()) == {
        "id", "stream_session_id", "user_id", "client_session_id",
        "joined_at", "lease_expires_at", "left_at", "created_at", "updated_at",
    }
    assert list(table.primary_key.columns.keys()) == ["id"]
    for column in table.c:
        assert column.nullable is (column.name == "left_at")
        if column.name.endswith("_at"):
            assert isinstance(column.type, sa.DateTime)
            assert column.type.timezone is True
        else:
            assert isinstance(column.type, postgresql.UUID)
    assert table.c.id.default.arg(None).version == 7
    assert table.c.updated_at.onupdate is not None
    assert sa.inspect(StreamViewerSession).relationships.stream_session.back_populates == (
        "viewer_sessions"
    )
    relationship = sa.inspect(StreamSession).relationships.viewer_sessions
    assert relationship.back_populates == "stream_session"
    assert relationship.passive_deletes is True


def test_migration_and_model_schema_match(schema):
    actual, expected = schema.table, StreamViewerSession.__table__
    assert list(actual.c.keys()) == list(expected.c.keys())
    dialect = postgresql.dialect()
    for column in expected.c:
        other = actual.c[column.name]
        original_type = other.info.get("postgresql_type", other.type)
        assert original_type.compile(dialect=dialect) == column.type.compile(dialect=dialect)
        assert other.nullable == column.nullable
        assert other.primary_key == column.primary_key
        assert str(other.server_default.arg if other.server_default else None) == str(
            column.server_default.arg if column.server_default else None
        )

    def constraints(table):
        return {
            (
                type(constraint).__name__, constraint.name,
                tuple(constraint.columns.keys()),
                str(constraint.sqltext) if isinstance(constraint, sa.CheckConstraint) else None,
                tuple((fk.target_fullname, fk.ondelete) for fk in constraint.elements)
                if isinstance(constraint, sa.ForeignKeyConstraint) else (),
            )
            for constraint in table.constraints
        }

    assert constraints(actual) == constraints(expected)
    assert {
        str(CreateIndex(index).compile(dialect=dialect)) for index in actual.indexes
    } == {
        str(CreateIndex(index).compile(dialect=dialect)) for index in expected.indexes
    }
    assert {index.name for index in actual.indexes} == {
        "ix_stream_viewer_sessions_user_id", "ix_stream_viewer_sessions_open_lease",
    }
    index = next(index for index in actual.indexes if "open_lease" in index.name)
    assert str(index.dialect_options["sqlite"]["where"]) == "left_at IS NULL"
    if schema.migration:
        assert schema.migration.revision == "202"
        assert schema.migration.down_revision == "201"


def test_unclosed_row_and_server_audit_defaults(schema):
    insert_row(schema)
    stored = schema.connection.execute(sa.select(schema.table)).mappings().one()
    assert stored["left_at"] is None
    assert stored["created_at"] is not None
    assert stored["updated_at"] is not None
    assert stored["joined_at"] == JOINED_AT.replace(tzinfo=None)
    assert stored["lease_expires_at"] == LEASE_EXPIRES_AT.replace(tzinfo=None)


@pytest.mark.parametrize("left_at", [JOINED_AT, LEASE_EXPIRES_AT])
def test_leave_at_interval_boundaries_is_valid(schema, left_at):
    insert_row(schema, left_at=left_at)


@pytest.mark.parametrize(
    "overrides",
    [
        {"lease_expires_at": JOINED_AT},
        {"lease_expires_at": JOINED_AT - timedelta(microseconds=1)},
        {"left_at": JOINED_AT - timedelta(microseconds=1)},
        {"left_at": LEASE_EXPIRES_AT + timedelta(microseconds=1)},
    ],
)
def test_invalid_timestamp_order_is_rejected(schema, overrides):
    with pytest.raises(IntegrityError):
        insert_row(schema, **overrides)
    schema.connection.rollback()


@pytest.mark.parametrize(
    "column", ["id", "stream_session_id", "user_id", "client_session_id",
               "joined_at", "lease_expires_at", "created_at", "updated_at"],
)
def test_required_columns_reject_null(schema, column):
    with pytest.raises(IntegrityError):
        insert_row(schema, **{column: None})
    schema.connection.rollback()


def test_join_retry_key_is_unique_even_after_leave(schema):
    row = insert_row(schema, left_at=LEASE_EXPIRES_AT)
    with pytest.raises(IntegrityError):
        insert_row(schema, client_session_id=row["client_session_id"])
    schema.connection.rollback()
    assert schema.connection.scalar(sa.select(sa.func.count()).select_from(schema.table)) == 1


def test_same_user_can_have_multiple_connections_and_reconnect_history(schema):
    insert_row(schema)
    insert_row(schema)
    insert_row(schema, left_at=LEASE_EXPIRES_AT)
    insert_row(
        schema, joined_at=LEASE_EXPIRES_AT + timedelta(seconds=10),
        lease_expires_at=LEASE_EXPIRES_AT + timedelta(seconds=70),
    )
    assert schema.connection.scalar(sa.select(sa.func.count()).select_from(schema.table)) == 4


def test_attempt_key_is_scoped_to_broadcast_and_user(schema):
    row = insert_row(schema)
    other_user, other_stream = uuid.uuid4(), uuid.uuid4()
    schema.connection.execute(schema.metadata.tables["users"].insert(), {"id": other_user})
    schema.connection.execute(
        schema.metadata.tables["stream_sessions"].insert(), {"id": other_stream}
    )
    insert_row(schema, user_id=other_user, client_session_id=row["client_session_id"])
    insert_row(schema, stream_session_id=other_stream, client_session_id=row["client_session_id"])


@pytest.mark.parametrize("column", ["stream_session_id", "user_id"])
def test_missing_parent_is_rejected(schema, column):
    with pytest.raises(IntegrityError):
        insert_row(schema, **{column: uuid.uuid4()})
    schema.connection.rollback()


@pytest.mark.parametrize("parent", ["users", "stream_sessions"])
def test_parent_deletion_cascades(schema, parent):
    insert_row(schema)
    table = schema.metadata.tables[parent]
    parent_id = schema.user_id if parent == "users" else schema.stream_id
    schema.connection.execute(table.delete().where(table.c.id == parent_id))
    schema.connection.commit()
    assert schema.connection.scalar(sa.select(sa.func.count()).select_from(schema.table)) == 0


def test_migration_downgrade_and_upgrade_round_trip(schema):
    if schema.migration is None:
        return
    insert_row(schema)
    schema.migration.downgrade()
    assert TABLE_NAME not in sa.inspect(schema.connection).get_table_names()
    assert {"users", "stream_sessions"} <= set(sa.inspect(schema.connection).get_table_names())
    schema.migration.upgrade()
    schema.table = schema.metadata.tables[TABLE_NAME]
    insert_row(schema)
    assert schema.connection.scalar(sa.select(sa.func.count()).select_from(schema.table)) == 1
