.PHONY: help lint format test

PYTHON ?= .venv/bin/python
TEST_FILE ?= tests/

help:
	@echo 'lint   - ruff check'
	@echo 'format - ruff format + isort'
	@echo 'test   - pytest (默认全部；TEST_FILE=tests/test_agent_graph.py 可收窄)'

lint:
	$(PYTHON) -m ruff check agents api evals tests tools utils services stores

format:
	$(PYTHON) -m ruff format agents api evals tests tools utils services stores
	$(PYTHON) -m ruff check --select I --fix agents api evals tests tools utils services stores

test:
	$(PYTHON) -m pytest $(TEST_FILE)
