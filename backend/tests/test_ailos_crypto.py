"""Testes para app.core.crypto (criptografia de tokens da integração Ailos)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import settings

VALID_KEY = Fernet.generate_key().decode()


class TestFernetConfig:
    def test_no_key_raises_crypto_error(self, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_token_encryption_key', '')

        with pytest.raises(crypto.CryptoError):
            crypto.encrypt_token('abc')

    def test_invalid_key_raises_crypto_error(self, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_token_encryption_key', 'not-a-valid-fernet-key')

        with pytest.raises(crypto.CryptoError):
            crypto.encrypt_token('abc')


class TestEncryptDecryptRoundTrip:
    def test_round_trip(self, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)
        token = 'super-secret-token-value'

        encrypted = crypto.encrypt_token(token)

        assert encrypted != token
        assert crypto.decrypt_token(encrypted) == token

    def test_decrypt_invalid_token_raises(self, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)

        with pytest.raises(crypto.CryptoError):
            crypto.decrypt_token('not-a-valid-encrypted-token')


class TestMaskToken:
    def test_empty_or_none_returns_empty_string(self):
        assert crypto.mask_token('') == ''
        assert crypto.mask_token(None) == ''

    def test_short_value_fully_masked(self):
        assert crypto.mask_token('ab') == '**'
        assert crypto.mask_token('abcd') == '****'

    def test_long_value_keeps_last_four_chars(self):
        assert crypto.mask_token('abcdefgh') == '****efgh'
