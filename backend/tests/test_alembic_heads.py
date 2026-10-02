"""The backend startup upgrades to 'head', which requires one Alembic head."""

from pathlib import Path

from alembic.script import ScriptDirectory


def test_migrations_have_one_head():
    alembic_dir = Path(__file__).resolve().parents[1] / 'alembic'
    heads = ScriptDirectory(str(alembic_dir)).get_heads()
    assert len(heads) == 1, f'Expected one Alembic head, found: {heads}'
