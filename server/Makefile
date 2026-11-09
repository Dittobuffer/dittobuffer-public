# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

.PHONY: install dev test lint run admin docker check-large-files clean

install:
	python -m pip install -r requirements.txt

dev:
	python -m pip install -r requirements-dev.txt
	python -m pip install -e .

test:
	python -m pytest -q

lint:
	ruff check src tests

run:
	python -m dittobuffer

admin:
	python -m dittobuffer /open

docker:
	docker compose up --build

check-large-files:
	python scripts/check-large-files.py

clean:
	rm -rf .pytest_cache .ruff_cache logs run
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
