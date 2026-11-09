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
