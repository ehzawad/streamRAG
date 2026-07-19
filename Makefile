NAIVE_BASE_URL ?= http://localhost:8001
STREAM_BASE_URL ?= http://localhost:8002
SMOKE_EVALUATION_DIR ?= data/crag_eval
APP_STATE_ROOT ?= var/dev-services
BENCH_DEV_STATE_ROOT ?= comparison/benchmark/results/dev-services
BENCH_EVALUATION_DIR ?= data/crag_eval
BENCH_INFERENCE_DIR ?= comparison/benchmark/results/inference_bundle
BENCH_STATE_ROOT ?= comparison/benchmark/results/services
BENCH_FINAL_DIR ?= comparison/benchmark/results/final
BENCH_PREDICTIONS ?= $(BENCH_FINAL_DIR)/predictions.jsonl
BENCH_SUMMARY ?= $(BENCH_FINAL_DIR)/summary.json
BENCH_ADJUDICATIONS ?= $(BENCH_FINAL_DIR)/manual_adjudications.jsonl
BENCH_QUERY_LIMIT ?= 10
BENCH_CASE_TIMEOUT_S ?= 45
BENCH_POST_TYPING_DWELL_MS ?= 5000
DEV_QUERY_LIMIT ?= 5
DEV_PREDICTIONS ?= comparison/benchmark/results/dev-comparison/predictions.jsonl
DEV_SUMMARY ?= comparison/benchmark/results/dev-comparison/summary.json

.PHONY: setup setup-python setup-frontend dev-naive dev-stream dev-frontend \
	check check-shared check-naive check-stream check-comparison \
	check-frontend build verify-data crag-source sync-naive sync-stream \
	sync-app \
	benchmark-inference-bundle benchmark-services-check benchmark-services-sync \
	benchmark-services-serve benchmark-dev-services-check benchmark-dev-services-sync \
	benchmark-dev-services-serve benchmark-smoke score-dev benchmark score score-final \
	docker-config docker-build docker-up docker-sync docker-down

setup: setup-python setup-frontend

setup-python:
	uv sync --frozen --python 3.14

setup-frontend:
	cd frontend && npm ci

dev-naive:
	mkdir -p $(APP_STATE_ROOT)/naive
	ALLOW_UNREVIEWED_DATASET=1 QDRANT_PATH=$(APP_STATE_ROOT)/naive/qdrant \
		RUNTIME_DB=$(APP_STATE_ROOT)/naive/runtime.sqlite3 \
		METRICS_LOG=$(APP_STATE_ROOT)/naive/requests.jsonl \
		uv run uvicorn naive.api:app --reload --host 127.0.0.1 --port 8001

dev-stream:
	mkdir -p $(APP_STATE_ROOT)/stream
	ALLOW_UNREVIEWED_DATASET=1 QDRANT_PATH=$(APP_STATE_ROOT)/stream/qdrant \
		RUNTIME_DB=$(APP_STATE_ROOT)/stream/runtime.sqlite3 \
		METRICS_LOG=$(APP_STATE_ROOT)/stream/requests.jsonl \
		uv run uvicorn stream.api:app --reload --host 127.0.0.1 --port 8002

dev-frontend:
	cd frontend && npm run dev -- --host 127.0.0.1

check-shared:
	uv run ruff check shared scripts
	uv run pytest -q shared/tests

check-naive:
	uv run ruff check shared naive
	uv run pytest -q shared/tests naive/tests

check-stream:
	uv run ruff check shared stream
	uv run pytest -q shared/tests stream/tests

check-comparison:
	uv run ruff check comparison
	uv run pytest -q comparison/tests

check-frontend:
	cd frontend && npm test
	cd frontend && npm run build

check:
	uv run ruff check shared naive stream comparison scripts
	uv run pytest -q
	cd frontend && npm test
	cd frontend && npm run build

build:
	uv build
	cd frontend && npm run build

verify-data:
	uv run python -m scripts.verify_dataset

crag-source:
	uv run python -m scripts.download_crag_source

sync-naive:
	curl --fail --show-error --request POST $(NAIVE_BASE_URL)/v1/data/sync

sync-stream:
	curl --fail --show-error --request POST $(STREAM_BASE_URL)/v1/data/sync

sync-app: sync-naive sync-stream

benchmark-inference-bundle:
	uv run python -m comparison.prepare_inference_bundle \
		--evaluation-dir $(BENCH_EVALUATION_DIR) --output-dir $(BENCH_INFERENCE_DIR)

benchmark-services-check:
	uv run python -m comparison.services check \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-services-sync:
	uv run python -m comparison.services sync \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-services-serve:
	uv run python -m comparison.services serve \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-dev-services-check:
	uv run python -m comparison.services check --development-candidate \
		--dataset-dir $(SMOKE_EVALUATION_DIR) --state-root $(BENCH_DEV_STATE_ROOT)

benchmark-dev-services-sync:
	uv run python -m comparison.services sync --development-candidate \
		--dataset-dir $(SMOKE_EVALUATION_DIR) --state-root $(BENCH_DEV_STATE_ROOT)

benchmark-dev-services-serve:
	uv run python -m comparison.services serve --development-candidate \
		--dataset-dir $(SMOKE_EVALUATION_DIR) --state-root $(BENCH_DEV_STATE_ROOT)

benchmark-smoke:
	uv run python -m comparison.benchmark.run_benchmark --smoke \
		--query-limit $(DEV_QUERY_LIMIT) --wpm 70 \
		--post-typing-dwell-ms $(BENCH_POST_TYPING_DWELL_MS) \
		--case-timeout-s $(BENCH_CASE_TIMEOUT_S) --max-typing-drift-ms 100 \
		--queries $(SMOKE_EVALUATION_DIR)/dev_queries.jsonl \
		--naive-base-url $(NAIVE_BASE_URL) --stream-base-url $(STREAM_BASE_URL) \
		--require-distinct-services --output $(DEV_PREDICTIONS)

score-dev:
	uv run python -m comparison.benchmark.score_dev \
		--dev $(SMOKE_EVALUATION_DIR)/dev_queries.jsonl \
		--predictions $(DEV_PREDICTIONS) --output $(DEV_SUMMARY)

benchmark:
	uv run python -m comparison.benchmark.run_benchmark \
		--warmup-repetitions 0 --repetitions 1 --query-limit $(BENCH_QUERY_LIMIT) \
		--wpm 70 --post-typing-dwell-ms $(BENCH_POST_TYPING_DWELL_MS) \
		--case-timeout-s $(BENCH_CASE_TIMEOUT_S) --max-typing-drift-ms 100 \
		--queries $(BENCH_INFERENCE_DIR)/test_queries.jsonl \
		--naive-base-url $(NAIVE_BASE_URL) --stream-base-url $(STREAM_BASE_URL) \
		--output $(BENCH_PREDICTIONS)

score:
	uv run python -m comparison.benchmark.score \
		--gold $(BENCH_EVALUATION_DIR)/test_gold.jsonl \
		--evaluation-manifest $(BENCH_EVALUATION_DIR)/checksums.sha256 \
		--predictions $(BENCH_PREDICTIONS) --output $(BENCH_SUMMARY)

score-final:
	uv run python -m comparison.benchmark.score \
		--gold $(BENCH_EVALUATION_DIR)/test_gold.jsonl \
		--evaluation-manifest $(BENCH_EVALUATION_DIR)/checksums.sha256 \
		--predictions $(BENCH_PREDICTIONS) --output $(BENCH_SUMMARY) \
		--adjudications $(BENCH_ADJUDICATIONS) --require-manual-adjudication

docker-config:
	docker compose config --quiet

docker-build: docker-config
	docker compose build

docker-up:
	docker compose up --build

docker-sync: sync-naive sync-stream

docker-down:
	docker compose down
