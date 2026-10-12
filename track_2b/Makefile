.PHONY: run run-remote eval test down

# Air-gapped end-to-end demo: fetch Apertus weights once, start llama.cpp on an internal
# network, run RedactGate + the synthetic Swiss dossier through it. Exit code 0 = checks passed.
run:
	docker compose up -d --wait llm
	docker compose run --rm --build demo

# Same demo against a hosted Apertus endpoint: LLM_BASE_URL, LLM_NAME, LLM_API_KEY.
run-remote:
	docker compose --profile remote run --rm --build demo-remote

# 30 generated de/fr/it client notes: leak rate (regex vs regex + Apertus NER) and task fidelity.
eval:
	docker compose up -d --wait llm
	docker compose run --rm --build demo python track_2b/src/eval_ch.py 30

# Unit tests of the whole gateway (no model, no network).
test:
	cd .. && python -m pytest -q

down:
	docker compose down
