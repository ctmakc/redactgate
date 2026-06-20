.PHONY: install dev test lint fmt up down logs psql eval seed-keys

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --port 8000

test:
	pytest -q

itest:
	RUN_INTEGRATION=1 pytest -q -m integration

lint:
	ruff check app tests

fmt:
	ruff check --fix app tests

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

psql:
	docker compose exec postgres psql -U redactgate redactgate

eval:
	python -m eval.harness

seed-keys:
	@python -c "import os,base64;print('VAULT_MASTER_KEY='+base64.b64encode(os.urandom(32)).decode())"
	@python -c "import os,base64;print('FINGERPRINT_HMAC_KEY='+base64.b64encode(os.urandom(32)).decode())"
	@python -c "import os,base64;print('AUDIT_HMAC_KEY='+base64.b64encode(os.urandom(32)).decode())"
