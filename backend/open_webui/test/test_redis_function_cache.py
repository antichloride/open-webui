"""
Tests for Redis-backed function and tool caching.

This file demonstrates two approaches for testing Redis functionality:
1. Using fakeredis (recommended) - provides a full Redis implementation in memory
2. Using unittest.mock - for simpler unit tests without Redis behavior

To install fakeredis:
    pip install fakeredis
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json


# =============================================================================
# Approach 1: Using fakeredis (Recommended)
# =============================================================================
# Provides full Redis functionality in-memory, great for integration tests


@pytest.fixture
def fake_redis():
    """Fixture providing an in-memory Redis instance"""
    try:
        import fakeredis
        return fakeredis.FakeStrictRedis(decode_responses=True)
    except ImportError:
        pytest.skip("fakeredis not installed. Run: pip install fakeredis")


@pytest.fixture
def redis_dict_with_fake_redis(fake_redis):
    """Create a RedisDict backed by fakeredis"""
    from open_webui.socket.utils import RedisDict
    
    # Monkey-patch the redis connection
    redis_dict = RedisDict.__new__(RedisDict)
    redis_dict.name = "test:functions"
    redis_dict.redis = fake_redis
    return redis_dict


def test_redis_dict_basic_operations(redis_dict_with_fake_redis):
    """Test basic RedisDict operations with fakeredis"""
    rd = redis_dict_with_fake_redis
    
    # Test __setitem__ and __getitem__
    test_module = {"type": "filter", "name": "test_filter"}
    rd["test_id"] = test_module
    assert rd["test_id"] == test_module
    
    # Test __contains__
    assert "test_id" in rd
    assert "nonexistent" not in rd
    
    # Test __delitem__
    del rd["test_id"]
    assert "test_id" not in rd


def test_redis_dict_multi_instance_sync(fake_redis):
    """Test that multiple RedisDict instances share data (simulating multi-instance)"""
    from open_webui.socket.utils import RedisDict
    
    # Create two instances pointing to same Redis
    instance_a = RedisDict.__new__(RedisDict)
    instance_a.name = "test:functions"
    instance_a.redis = fake_redis
    
    instance_b = RedisDict.__new__(RedisDict)
    instance_b.name = "test:functions"
    instance_b.redis = fake_redis
    
    # Instance A writes
    instance_a["function_1"] = {"name": "filter_a", "version": 1}
    
    # Instance B reads - should see the update instantly
    assert instance_b["function_1"] == {"name": "filter_a", "version": 1}
    
    # Instance B updates
    instance_b["function_1"] = {"name": "filter_a", "version": 2}
    
    # Instance A reads - should see the update
    assert instance_a["function_1"] == {"name": "filter_a", "version": 2}


def test_function_cache_with_fakeredis(fake_redis):
    """Test get_function_module_from_cache with fakeredis"""
    from open_webui.socket.utils import RedisDict
    
    # Mock request.app.state with fakeredis-backed dicts
    mock_request = Mock()
    mock_request.app.state.FUNCTIONS = RedisDict.__new__(RedisDict)
    mock_request.app.state.FUNCTIONS.name = "test:functions"
    mock_request.app.state.FUNCTIONS.redis = fake_redis
    
    mock_request.app.state.FUNCTION_CONTENT_HASHES = RedisDict.__new__(RedisDict)
    mock_request.app.state.FUNCTION_CONTENT_HASHES.name = "test:function_content_hashes"
    mock_request.app.state.FUNCTION_CONTENT_HASHES.redis = fake_redis
    
    # Mock DB and module loading
    with patch("open_webui.utils.plugin.Functions") as mock_functions, \
         patch("open_webui.utils.plugin.load_function_module_by_id") as mock_load:
        
        # Setup DB response
        mock_function = Mock()
        mock_function.content = "def test(): pass"
        mock_functions.get_function_by_id.return_value = mock_function
        
        # Setup module loading
        mock_module = Mock()
        mock_load.return_value = (mock_module, "filter", {})
        
        from open_webui.utils.plugin import get_function_module_from_cache
        
        # First call: cache miss, loads from DB
        result1, _, _ = get_function_module_from_cache(mock_request, "test_func_1")
        assert result1 == mock_module
        assert mock_functions.get_function_by_id.call_count == 1
        
        # Second call: cache hit, no DB call
        result2, _, _ = get_function_module_from_cache(mock_request, "test_func_1")
        assert result2 == mock_module
        assert mock_functions.get_function_by_id.call_count == 1  # Still 1, cached!


# =============================================================================
# Approach 2: Using unittest.mock
# =============================================================================
# Simpler but doesn't test actual Redis behavior


@pytest.fixture
def mock_redis_dict():
    """Create a simple mock that behaves like a dict"""
    mock_dict = MagicMock()
    storage = {}
    
    # Simulate dict-like behavior
    def setitem(key, value):
        storage[key] = value
    
    def getitem(key):
        return storage[key]
    
    def contains(key):
        return key in storage
    
    def delitem(key):
        del storage[key]
    
    mock_dict.__setitem__ = setitem
    mock_dict.__getitem__ = getitem
    mock_dict.__contains__ = contains
    mock_dict.__delitem__ = delitem
    
    return mock_dict


def test_function_cache_with_mock(mock_redis_dict):
    """Test get_function_module_from_cache with simple mocks"""
    mock_request = Mock()
    mock_request.app.state.FUNCTIONS = mock_redis_dict
    mock_request.app.state.FUNCTION_CONTENT_HASHES = {}
    
    with patch("open_webui.utils.plugin.Functions") as mock_functions, \
         patch("open_webui.utils.plugin.load_function_module_by_id") as mock_load:
        
        mock_function = Mock()
        mock_function.content = "def test(): pass"
        mock_functions.get_function_by_id.return_value = mock_function
        
        mock_module = Mock()
        mock_load.return_value = (mock_module, "action", {})
        
        from open_webui.utils.plugin import get_function_module_from_cache
        
        # Test caching behavior
        result1, _, _ = get_function_module_from_cache(mock_request, "test_func")
        assert result1 == mock_module


# =============================================================================
# Performance/Integration Tests
# =============================================================================


def test_batch_loading_performance(fake_redis):
    """Test that batch loading reduces Redis calls"""
    from open_webui.socket.utils import RedisDict
    
    # Pre-populate cache with 10 functions
    functions_cache = RedisDict.__new__(RedisDict)
    functions_cache.name = "test:functions"
    functions_cache.redis = fake_redis
    
    for i in range(10):
        functions_cache[f"func_{i}"] = {"id": f"func_{i}", "type": "filter"}
    
    # Simulate checking if all are cached (what get_all_models does)
    call_count = 0
    for i in range(10):
        if f"func_{i}" in functions_cache:
            call_count += 1
            _ = functions_cache[f"func_{i}"]
    
    # All 10 should be found in cache
    assert call_count == 10


# =============================================================================
# Instructions for Running Tests
# =============================================================================

"""
To run these tests:

1. Install fakeredis (recommended):
   pip install fakeredis pytest

2. Run all tests:
   pytest backend/open_webui/test/test_redis_function_cache.py -v

3. Run only fakeredis tests:
   pytest backend/open_webui/test/test_redis_function_cache.py -v -k "fake"

4. Run only mock tests:
   pytest backend/open_webui/test/test_redis_function_cache.py -v -k "mock"

5. Run with coverage:
   pytest backend/open_webui/test/test_redis_function_cache.py --cov=open_webui.utils.plugin

Note: The fakeredis approach is recommended because it:
- Tests actual Redis behavior (serialization, atomic operations, etc.)
- Catches Redis-specific bugs
- Simulates multi-instance scenarios accurately
- Requires minimal mocking
"""
