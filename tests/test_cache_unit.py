import pytest

from app.cache import JsonCache
from tests.fakes import MemoryRedis


def test_key_format_and_validation():
    cache = JsonCache(MemoryRedis(), prefix="lab2:12345:svc", ttl=5, resource_name="ticket")
    assert cache.key("TCK-0001") == "lab2:12345:svc:ticket:TCK-0001"
    with pytest.raises(ValueError):
        JsonCache(MemoryRedis(), prefix="p", ttl=0, resource_name="ticket")
    with pytest.raises(ValueError):
        JsonCache(MemoryRedis(), prefix="", ttl=5, resource_name="ticket")


def test_all_three_operations_survive_outage():
    client = MemoryRedis()
    cache = JsonCache(client, prefix="p", ttl=5, resource_name="r")
    assert cache.put("1", {"a": 1}) is True
    client.down = True
    assert cache.get("1") == (None, "BYPASS")
    assert cache.put("1", {"a": 2}) is False
    assert cache.delete("1") is False
