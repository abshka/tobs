# TOBS Test Suite

Comprehensive test coverage for the Telegram Obsidian Bot System (TOBS).

## Quick Start

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=html

# Run specific test file
uv run pytest tests/core/test_connection_manager_init.py -v

# Run tests for specific module
uv run pytest tests/core/ -v

# Run with detailed output
uv run pytest -vv --tb=short
```

## Test Structure

```
tests/
├── conftest.py                     # Shared fixtures and configuration
├── core/                           # Core infrastructure tests
│   ├── test_cache*.py              # Cache system tests (~40 tests)
│   ├── test_connection_manager_*.py # ConnectionManager tests (110 tests)
│   └── test_*.py                   # Other core component tests
├── media/                          # Media processing tests
│   ├── test_downloader.py
│   ├── test_processors_*.py
│   ├── test_validators.py
│   └── test_metadata.py
└── README.md                       # This file
```

## Test Coverage

### Excellent Coverage (>90%)
- ✅ `src/core/cache.py` — 97.95%
- ✅ `src/core/connection.py` — 95.75%
- ✅ `src/media/cache.py` — 93.33%

### Good Coverage (80-90%)
- ✅ `src/media/metadata.py` — 87.10%
- ✅ `src/media/processors/base.py` — 86.67%
- ✅ `src/media/validators.py` — 83.33%

### Overall Project
- **Total Tests:** ~230
- **Overall Coverage:** 34.87%
- **Core Infrastructure:** 93.74%

See `TESTING_STATUS.md` for detailed coverage report.

## Test Organization

### Connection Manager Tests (Session 7)

The ConnectionManager test suite is organized into focused batches:

1. **test_connection_manager_init.py** (15 tests)
   - ConnectionManager initialization
   - Pool creation and configuration
   - Properties and backwards compatibility

2. **test_connection_manager_stats.py** (12 tests)
   - OperationStats lifecycle
   - Success/failure tracking
   - Speed recording and averaging

3. **test_connection_manager_retry.py** (20 tests)
   - Backoff strategies (FIXED, LINEAR, EXPONENTIAL, ADAPTIVE)
   - Timeout calculation
   - Jitter and max delay

4. **test_connection_manager_throttle.py** (15 tests)
   - Throttling detection
   - Delay calculation
   - Detection window logic

5. **test_connection_manager_telegram_errors.py** (12 tests)
   - FloodWaitError handling
   - SlowModeWaitError handling
   - Timeout and RPC errors

6. **test_connection_manager_execute_retry.py** (19 tests) ⭐
   - End-to-end retry orchestration
   - Integration of all components
   - Pool selection and stats updates

7. **test_connection_manager_download_progress.py** (17 tests)
   - Progress tracking lifecycle
   - Speed calculation
   - Stall detection

## Writing Tests

### Test Structure

Follow the Arrange-Act-Assert pattern:

```python
@pytest.mark.asyncio
async def test_something(fixture):
    """Clear description of what this test validates."""
    # Arrange
    setup_data = prepare_test_data()
    
    # Act
    result = await perform_operation(setup_data)
    
    # Assert
    assert result == expected_value
```

### Naming Conventions

- **Test files:** `test_<module_name>.py`
- **Test classes:** `Test<FeatureName>`
- **Test functions:** `test_<specific_behavior>`

### Using Fixtures

Common fixtures in `conftest.py`:

```python
# ConnectionManager instance with cleanup
async def test_something(connection_manager):
    result = await connection_manager.execute_with_retry(...)
    assert result == expected

# Isolated AdaptiveTaskPool
async def test_pool_behavior(adaptive_pool):
    await adaptive_pool.submit(operation)
```

### Mocking Best Practices

1. **Patch at module level:**
   ```python
   patch("src.core.connection.time.time")  # ✅ Correct
   patch("time.time")                      # ❌ Wrong
   ```

2. **Use AsyncMock for async functions:**
   ```python
   with patch.object(obj, 'async_method', new_callable=AsyncMock):
       ...
   ```

3. **Mock telethon errors:**
   ```python
   flood_error = MagicMock(spec=FloodWaitError)
   flood_error.seconds = 30
   ```

## Running Specific Test Groups

```bash
# Run all ConnectionManager tests
uv run pytest tests/core/test_connection_manager_*.py -v

# Run only retry logic tests
uv run pytest tests/core/test_connection_manager_retry.py -v

# Run tests matching pattern
uv run pytest -k "retry" -v

# Run tests with coverage for specific file
uv run pytest --cov=src/core/connection --cov-report=term-missing
```

## Debugging Tests

### Verbose Output
```bash
# Show all output (print statements)
uv run pytest -v -s

# Show locals on failure
uv run pytest -v --tb=long

# Stop on first failure
uv run pytest -x

# Run last failed tests only
uv run pytest --lf
```

### Coverage Reports

```bash
# HTML report (opens in browser)
uv run pytest --cov=src --cov-report=html
open htmlcov/index.html

# Terminal report with missing lines
uv run pytest --cov=src --cov-report=term-missing

# Only coverage for specific module
uv run pytest --cov=src/core/connection --cov-report=term-missing
```

## CI/CD Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    uv run pytest --cov=src --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v2
  with:
    file: ./coverage.xml
```

## Test Quality Guidelines

### All Tests Must:
1. ✅ Be independent (no shared state between tests)
2. ✅ Be fast (< 100ms per test)
3. ✅ Have clear names describing behavior
4. ✅ Clean up resources (use fixtures for cleanup)
5. ✅ Mock external dependencies (filesystem, network, time)

### Tests Should:
- Focus on one behavior per test
- Use descriptive assertion messages
- Test edge cases and error conditions
- Be grouped in classes by feature

## Common Test Patterns

### Testing Async Functions
```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None
```

### Testing Retries
```python
with patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock):
    # Sleep is mocked, retries happen instantly
    result = await operation_with_retry()
```

### Testing Time-Dependent Code
```python
with patch("src.core.connection.time.time") as mock_time:
    mock_time.side_effect = [1000.0, 1001.0, 1002.0]
    # Each time.time() call returns next value
```

## Troubleshooting

### "RuntimeWarning: coroutine was never awaited"
- Make sure async functions are called with `await`
- Use `new_callable=AsyncMock` when patching async methods

### "StopIteration" or "side_effect exhausted"
- Provide enough values in `side_effect` list
- Or use `return_value` for constant returns

### Tests pass individually but fail together
- Check for shared state in module-level variables
- Use fixtures to ensure clean state

### Coverage not showing
- Ensure pytest-cov is installed: `uv pip install pytest-cov`
- Check that source path is correct relative to project root

## Performance

**Current Performance:**
- 230 tests run in ~10 seconds
- Average: ~43ms per test
- No flaky tests
- 100% pass rate

## Contributing

When adding new tests:

1. Run existing tests to ensure nothing breaks
2. Follow naming and structure conventions
3. Add docstrings to test functions
4. Mock external dependencies
5. Aim for >80% coverage for new code
6. Group related tests in classes
7. Update this README if adding new test files

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [Python unittest.mock](https://docs.python.org/3/library/unittest.mock.html)

## Support

For questions or issues with tests:
1. Check `TESTING_STATUS.md` for coverage status
2. Review `SESSION_7_COMPLETE.md` for ConnectionManager test details
3. Look at similar existing tests as examples

---

**Test Status:** ✅ All passing
**Coverage:** 34.87% overall, 95.75% for core infrastructure
**Last Updated:** Session 7 completion
