"""Opt-in migration validation confined to a uniquely named disposable database."""

import os
from io import StringIO
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.models.social import Playlist
from app.models.user import AccountStatus, Profile, User, UserRole


def migration_config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def test_canonical_migration_graph_has_one_head_and_ordered_beta_tail():
    script = ScriptDirectory.from_config(migration_config())
    assert script.get_heads() == ["217"]
    assert script.get_bases() == ["001"]
    assert [
        revision.revision for revision in script.iterate_revisions("head", "206")
    ] == [
        "217", "216", "215", "214", "213", "212", "211", "210", "209", "208", "207"
    ]
    assert len(list(script.walk_revisions())) == 43


def test_alembic_cli_works_from_backend_directory():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "heads"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "217 (head)"


def test_migration_configuration_accepts_percent_encoded_credentials(monkeypatch):
    monkeypatch.setattr(
        settings, "database_url_sync",
        "postgresql+psycopg://test:p%25ss@localhost/disposable",
    )
    config = migration_config()
    config.output_buffer = StringIO()
    command.upgrade(config, "210:211", sql=True)
    assert "onboarding_collab_types" in config.output_buffer.getvalue()
    assert config.get_main_option("sqlalchemy.url") == settings.database_url_sync


@pytest.mark.parametrize("enum_preexists", [False, True])
def test_collaboration_payment_enum_is_created_once_at_revision_214(
    disposable_database, enum_preexists
):
    engine = disposable_database
    config = migration_config()
    command.upgrade(config, "213")

    if enum_preexists:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TYPE collaboration_payment_state AS ENUM "
                "('PENDING', 'AUTHORIZED_HELD', 'COLLABORATION_ACTIVE', "
                "'COMPLETED', 'RELEASE_PENDING', 'RELEASED', 'CANCELLED', "
                "'REFUNDED', 'DISPUTED')"
            ))

    command.upgrade(config, "214")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "214"
        payment_state = next(
            enum
            for enum in inspect(connection).get_enums()
            if enum["name"] == "collaboration_payment_state"
        )
        assert payment_state["labels"] == [
            "PENDING",
            "AUTHORIZED_HELD",
            "COLLABORATION_ACTIVE",
            "COMPLETED",
            "RELEASE_PENDING",
            "RELEASED",
            "CANCELLED",
            "REFUNDED",
            "DISPUTED",
        ]


@pytest.fixture
def disposable_database(monkeypatch):
    source_url = os.environ.get("BETA_TEST_DATABASE_URL")
    if not source_url:
        pytest.skip("Set BETA_TEST_DATABASE_URL for disposable PostgreSQL validation")
    admin_url = make_url(source_url).set(drivername="postgresql+psycopg")
    name = f"beta_migrations_{uuid.uuid4().hex}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        target_url = admin_url.set(database=name)
        engine = create_engine(target_url)
        monkeypatch.setattr(
            settings, "database_url_sync", target_url.render_as_string(hide_password=False)
        )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


@pytest.mark.parametrize("missing_legacy_constraint", [False, True])
@pytest.mark.parametrize("preexisting_playlist", [False, True])
def test_fresh_upgrade_and_beta_tail_rollback(
    disposable_database, missing_legacy_constraint, preexisting_playlist
):
    engine = disposable_database
    config = migration_config()
    playlist_id = uuid.uuid4()
    playlist_table = Playlist.__table__
    user_table = User.__table__
    profile_table = Profile.__table__
    assert isinstance(playlist_table, Table)
    assert isinstance(user_table, Table)
    assert isinstance(profile_table, Table)
    if missing_legacy_constraint:
        command.upgrade(config, "5992447b5c45")
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE collaboration_participants "
                "DROP CONSTRAINT uq_collaboration_participant"
            ))
    if preexisting_playlist:
        command.upgrade(config, "212")
        playlist_table.create(engine)
        user_id, profile_id = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(user_table.insert().values(
                id=user_id, email="beta@example.test", username="beta",
                hashed_password="fixture", role=UserRole.USER, status=AccountStatus.ACTIVE,
            ))
            connection.execute(profile_table.insert().values(
                id=profile_id, user_id=user_id, display_name="Beta",
            ))
            connection.execute(playlist_table.insert().values(
                id=playlist_id, profile_id=profile_id, title="Existing playlist",
                cover="", item_label="items", plays=7,
            ))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "217"
        inspector = inspect(connection)
        assert {
            "paid_campaigns", "paid_deliveries", "paid_interaction_contexts",
            "categories", "profile_categories", "collaboration_payments",
        }.issubset(inspector.get_table_names())
        assert {
            "hold_idempotency_key",
            "authorization_reference",
            "authorized_by_operation",
            "hold_reversed_at",
            "hold_reversed_by_operation",
            "completion_confirmed_by_operation",
            "release_authorized_by_operation",
            "cancelled_by_operation",
            "refund_authorized_at",
            "refund_authorized_by_operation",
            "disputed_by_user_id",
            "disputed_by_operation",
            "dispute_resolved_at",
            "dispute_resolved_by_operation",
        }.issubset(
            {
                column["name"]
                for column in inspector.get_columns("collaboration_payments")
            }
        )
        assert "onboarding_collab_types" in {
            column["name"] for column in inspector.get_columns("profiles")
        }
        assert connection.execute(text("SELECT count(*) FROM categories")).scalar() == 9
        assert all(
            foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in inspector.get_foreign_keys("profile_categories")
        )
        assert any(
            set(constraint["column_names"]) == {"collaboration_id", "user_id"}
            for constraint in inspector.get_unique_constraints("collaboration_participants")
        )
        assert connection.execute(playlist_table.select().limit(0)).all() == []
        if preexisting_playlist:
            assert connection.execute(text(
                "SELECT plays FROM playlists WHERE id = :id"
            ), {"id": playlist_id}).scalar() == 7
    command.downgrade(config, "206")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "206"
        assert "categories" not in inspect(connection).get_table_names()
        assert "playlists" in inspect(connection).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "217"
        assert connection.execute(text("SELECT count(*) FROM categories")).scalar() == 9
        if preexisting_playlist:
            assert connection.execute(text(
                "SELECT plays FROM playlists WHERE id = :id"
            ), {"id": playlist_id}).scalar() == 7
