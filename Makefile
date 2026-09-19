# red-rail task runner. Every target is what CI runs, nothing more.

.PHONY: sync lint test check ci audit skills-install

RAIL_FLAGS ?=
DATE ?= $(shell date +%F)

## Install the project with every extra (dev, brain, reviewer)
sync:
	uv sync --all-extras

## Lint and format check
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Run the test suite
test:
	uv run pytest -q

## Run the rail gates against this repository (dogfooding; RAIL_FLAGS=--ci in CI)
check:
	uv run rail check $(RAIL_FLAGS)

## What CI runs, in order
ci: lint test check

## Audit every ReD project and keep the dated snapshot (the drift table, versioned)
audit:
	mkdir -p docs/audits
	uv run rail audit .. --json > docs/audits/$(DATE)-projects.json
	uv run rail audit .. > docs/audits/$(DATE)-projects.md
	uv run rail audit ..

## Symlink the facade skills into ~/.claude/skills (operator's workstation)
skills-install:
	mkdir -p $(HOME)/.claude/skills
	for d in skills/*/; do ln -sfn "$(CURDIR)/$$d" "$(HOME)/.claude/skills/$$(basename $$d)"; done
