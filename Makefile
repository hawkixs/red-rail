# red-rail task runner. Every target is what CI runs, nothing more.

.PHONY: sync lint test check ci

## Install the project and its dev extras
sync:
	uv sync --extra dev

## Lint and format check
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Run the test suite
test:
	uv run pytest -q

## Run the rail gates against this repository (dogfooding)
check:
	uv run rail check

## What CI runs, in order
ci: lint test check
