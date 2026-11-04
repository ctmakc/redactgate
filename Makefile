.PHONY: install dev test lint fmt up down logs psql eval seed-keys near-contract near-contract-test near-setup near-demo

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

# ── NEAR audit anchor (NEAR Hackathon 2026) ──────────────────────────────────
near-contract:
	cd contracts/audit-anchor && RUSTFLAGS="-C link-arg=-s -C target-cpu=mvp" \
	  cargo build --release --target wasm32-unknown-unknown && \
	  cp $${CARGO_TARGET_DIR:-target}/wasm32-unknown-unknown/release/redactgate_audit_anchor.wasm res/audit_anchor.wasm

near-contract-test:
	cd contracts/audit-anchor && cargo test

near-setup:
	python scripts/near_setup.py

near-demo:
	set -a && . ./.env.near && set +a && python scripts/near_anchor_demo.py
