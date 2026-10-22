import io
import sqlite3

from alembic import command
from alembic.config import Config

from enterprise_ai_api.database import PROJECT_ROOT


def config_for(url, output=None):
    config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_upgrade_preserves_published_versions_chunks_and_acl(tmp_path):
    database = tmp_path / "legacy.db"
    config = config_for(f"sqlite:///{database}")
    command.upgrade(config, "20261022_0011")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO tenants (id, name, status) VALUES ('a', 'Legacy', 'active')"
        )
        connection.execute(
            "INSERT INTO users (id, external_subject, status) VALUES ('b', 'legacy', 'active')"
        )
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) "
            "VALUES ('a', 'b', 'enterprise_admin')"
        )
        connection.execute(
            "INSERT INTO spaces (id, tenant_id, name, classification) "
            "VALUES ('c', 'a', 'Space', 'internal')"
        )
        connection.execute(
            "INSERT INTO documents (id, tenant_id, space_id, title, created_by) "
            "VALUES ('d', 'a', 'c', 'Doc', 'b')"
        )
        connection.execute(
            "INSERT INTO document_versions (id, tenant_id, document_id, version_number, "
            "filename, media_type, byte_size, content_hash, object_key, status, created_by) "
            "VALUES ('e', 'a', 'd', 1, 'a.txt', 'text/plain', 1, 'hash', 'key', 'published', 'b')"
        )
        connection.execute(
            "INSERT INTO document_chunks "
            "(id, tenant_id, document_version_id, position, locator, text) "
            "VALUES ('f', 'a', 'e', 1, 'paragraph:1', 'legacy content')"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT status FROM document_versions").fetchall() == [
            ("published",)
        ]
        assert connection.execute("SELECT text FROM document_chunks").fetchall() == [
            ("legacy content",)
        ]
        assert connection.execute(
            "SELECT active, provisioning_active FROM memberships"
        ).fetchall() == [(1, 1)]
        connection.execute("UPDATE document_versions SET status='queued'")


def test_postgres_migration_generates_forced_tenant_policies():
    stream = io.StringIO()
    config = config_for("postgresql+psycopg://unused/unused", stream)
    command.upgrade(config, "20261022_0011:head", sql=True)
    sql = stream.getvalue()
    for table in ("ingestion_jobs", "federated_identities", "federated_groups"):
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}"' in sql
