"""
Testes do AuditMiddleware.

Mocka _write_log para verificar que o middleware extrai e passa
corretamente os dados para todos os perfis de usuário.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db, Base
from app.models.enums import UserRole
from app.models.user import User
from app.core.security import create_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _user(role: UserRole, uid: int = 1) -> User:
    return User(
        id=uid,
        name=f'Test {role.value}',
        email=f'{role.value}@test.local',
        role=role,
        active=True,
        is_deleted=False,
        password_hash='hash',
    )


def _token(user: User) -> str:
    """Gera token com name e role embutidos (formato atual)."""
    role_str = user.role.value if hasattr(user.role, 'value') else str(user.role)
    return create_access_token(str(user.id), name=user.name, role=role_str)


def _make_client(role: UserRole, uid: int = 1):
    db = _make_db()
    u = _user(role, uid)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: u
    return TestClient(app, raise_server_exceptions=False), u


PATCH = 'app.core.audit._write_log'


# ---------------------------------------------------------------------------
# Testes por perfil
# ---------------------------------------------------------------------------

class TestAuditRegistraTodosPerfis:

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_admin_é_registrado(self):
        client, user = _make_client(UserRole.ADMIN, 1)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/dashboard', headers={'Authorization': f'Bearer {token}'})

        assert mock_write.called
        # Pode ser chamado mais de uma vez em caso de redirect
        # Verificamos o último call (resposta final)
        user_id, user_name, user_role = mock_write.call_args[0][:3]
        assert user_id == 1
        assert user_name == user.name
        assert user_role == UserRole.ADMIN.value

    def test_operacional_é_registrado(self):
        client, user = _make_client(UserRole.OPERATIONAL, 2)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/trackers/', headers={'Authorization': f'Bearer {token}'})

        mock_write.assert_called_once()
        assert mock_write.call_args[0][2] == UserRole.OPERATIONAL.value

    def test_financeiro_é_registrado(self):
        client, user = _make_client(UserRole.FINANCIAL, 3)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/contracts/', headers={'Authorization': f'Bearer {token}'})

        mock_write.assert_called_once()
        assert mock_write.call_args[0][2] == UserRole.FINANCIAL.value

    def test_sem_token_nao_registra(self):
        client, _ = _make_client(UserRole.ADMIN, 1)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/dashboard')  # sem Authorization

        mock_write.assert_not_called()

    def test_token_invalido_nao_registra(self):
        client, _ = _make_client(UserRole.ADMIN, 1)

        with patch(PATCH) as mock_write:
            client.get(
                '/api/v1/dashboard',
                headers={'Authorization': 'Bearer token_invalido_aqui'},
            )

        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Testes de extração de dados
# ---------------------------------------------------------------------------

class TestAuditExtracaoDados:

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_entity_type_cliente_extraído(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/clients/', headers={'Authorization': f'Bearer {token}'})

        # args: user_id, user_name, user_role, method, path, entity_type, entity_id, status_code, ip, description
        entity_type = mock_write.call_args[0][5]
        assert entity_type == 'cliente'

    def test_entity_type_veiculo_extraído(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/vehicles/', headers={'Authorization': f'Bearer {token}'})

        entity_type = mock_write.call_args[0][5]
        assert entity_type == 'veiculo'

    def test_entity_type_rastreador_extraído(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/trackers/', headers={'Authorization': f'Bearer {token}'})

        entity_type = mock_write.call_args[0][5]
        assert entity_type == 'rastreador'

    def test_entity_id_extraído(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/clients/42', headers={'Authorization': f'Bearer {token}'})

        entity_id = mock_write.call_args[0][6]
        assert entity_id == 42

    def test_status_code_404_capturado(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/clients/99999', headers={'Authorization': f'Bearer {token}'})

        status_code = mock_write.call_args[0][7]
        assert status_code == 404

    def test_status_code_200_capturado(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/dashboard', headers={'Authorization': f'Bearer {token}'})

        status_code = mock_write.call_args[0][7]
        assert status_code == 200

    def test_method_get(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/dashboard', headers={'Authorization': f'Bearer {token}'})

        method = mock_write.call_args[0][3]
        assert method == 'GET'

    def test_method_post(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.post('/api/v1/clients/', json={},
                        headers={'Authorization': f'Bearer {token}'})

        method = mock_write.call_args[0][3]
        assert method == 'POST'

    def test_method_delete(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.delete('/api/v1/users/99',
                          headers={'Authorization': f'Bearer {token}'})

        method = mock_write.call_args[0][3]
        assert method == 'DELETE'

    def test_descricao_legivel(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/vehicles/', headers={'Authorization': f'Bearer {token}'})

        description = mock_write.call_args[0][9]
        assert 'Consultou' in description
        assert 'veiculo' in description

    def test_descricao_com_id(self):
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/trackers/7',
                       headers={'Authorization': f'Bearer {token}'})

        description = mock_write.call_args[0][9]
        assert 'rastreador #7' in description

    def test_audit_logs_nao_registrado(self):
        """O endpoint de auditoria não deve gerar log (evita loop)."""
        client, user = _make_client(UserRole.ADMIN)
        token = _token(user)

        with patch(PATCH) as mock_write:
            client.get('/api/v1/audit-logs',
                       headers={'Authorization': f'Bearer {token}'})

        mock_write.assert_not_called()
