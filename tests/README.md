Notes:
- These tests are written for pytest and rely on monkeypatch and unittest.mock.
- They dynamically load the source module from tests/test_prompt.py to avoid pytest collection conflicts.
- Run with: pytest -q