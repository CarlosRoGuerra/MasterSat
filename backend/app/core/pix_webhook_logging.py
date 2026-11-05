"""Não registrar o segredo opaco usado na URL de callback bancário."""
import logging
import re

_URL = re.compile(r'(/sicredi-pix/webhooks/)[^/\s?]+')


def redigir(value):
    return _URL.sub(r'\1[redigido]', value) if isinstance(value, str) else value


class PixWebhookLogFilter(logging.Filter):
    def filter(self, record):
        record.msg = redigir(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redigir(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redigir(value) for key, value in record.args.items()}
        return True
