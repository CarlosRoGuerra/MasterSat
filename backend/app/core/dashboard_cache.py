"""Small, permission-scoped Redis cache for aggregate dashboard responses."""

import json
import logging

from redis import Redis, RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)
_PREFIX = 'mastersat:dashboard:v1:'
_ROLES = ('admin', 'operacional', 'financeiro')
_redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)


def _enabled() -> bool:
    # The SQLite test suite must remain independent of a running Redis server.
    return not settings.database_url.startswith('sqlite')


def get_dashboard(role: str) -> dict | None:
    if not _enabled():
        return None
    try:
        data = _redis.get(f'{_PREFIX}{role}')
        return json.loads(data) if data else None
    except (RedisError, ValueError):
        logger.warning('Dashboard cache read failed; querying database', exc_info=True)
        return None


def put_dashboard(role: str, payload: dict) -> None:
    if not _enabled():
        return
    try:
        _redis.setex(f'{_PREFIX}{role}', 20, json.dumps(payload))
    except RedisError:
        logger.warning('Dashboard cache write failed; response was still served', exc_info=True)


def invalidate_dashboard() -> None:
    if not _enabled():
        return
    try:
        _redis.delete(*(f'{_PREFIX}{role}' for role in _ROLES))
    except RedisError:
        logger.warning('Dashboard cache invalidation failed; TTL is at most 20s', exc_info=True)
