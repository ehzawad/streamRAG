NAIVE_BASE_URL ?= http://localhost:8001
STREAM_BASE_URL ?= http://localhost:8002
DEV_NAIVE_BASE_URL ?= http://localhost:8001
DEV_STREAM_BASE_URL ?= http://localhost:8002
SMOKE_EVALUATION_DIR ?= data/crag_eval
BENCH_EVALUATION_DIR ?= data/crag_eval
BENCH_INFERENCE_DIR ?= bench/results/inference_bundle
BENCH_STATE_ROOT ?= bench/results/services
BENCH_FINAL_DIR ?= bench/results/final
BENCH_PREDICTIONS ?= $(BENCH_FINAL_DIR)/predictions.jsonl
BENCH_SUMMARY ?= $(BENCH_FINAL_DIR)/summary.json
BENCH_ADJUDICATIONS ?= $(BENCH_FINAL_DIR)/manual_adjudications.jsonl
BENCH_QUERY_LIMIT ?= 10
BENCH_CASE_TIMEOUT_S ?= 45
DEV_QUERY_LIMIT ?= 5
DEV_PREDICTIONS ?= bench/results/dev-comparison/predictions.jsonl
DEV_SUMMARY ?= bench/results/dev-comparison/summary.json

.PHONY: setup dev check build verify-data sync-data benchmark-services-check \
	benchmark-inference-bundle benchmark-services-sync benchmark-services-serve \
	benchmark-smoke score-dev benchmark score score-final crag-source docker-up docker-down

setup:
	uv sync --frozen --python 3.14
	cd frontend && npm ci

dev:
	./scripts/dev.sh

check:
	uv run ruff check app scripts tests bench
	uv run pytest -q
	cd frontend && npm run build

build:
	uv build
	cd frontend && npm run build

verify-data:
	uv run python scripts/verify_dataset.py

crag-source:
	uv run python scripts/download_crag_source.py

sync-data:
	curl --fail --show-error --request POST http://localhost:8000/v1/data/sync

benchmark-inference-bundle:
	uv run python scripts/prepare_inference_bundle.py \
		--evaluation-dir $(BENCH_EVALUATION_DIR) --output-dir $(BENCH_INFERENCE_DIR)

benchmark-services-check:
	uv run python scripts/benchmark_services.py check \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-services-sync:
	uv run python scripts/benchmark_services.py sync \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-services-serve:
	uv run python scripts/benchmark_services.py serve \
		--dataset-dir $(BENCH_INFERENCE_DIR) --state-root $(BENCH_STATE_ROOT)

benchmark-smoke:
	uv run python bench/run_benchmark.py --smoke --query-limit $(DEV_QUERY_LIMIT) --wpm 70 \
		--case-timeout-s $(BENCH_CASE_TIMEOUT_S) --max-typing-drift-ms 100 \
		--queries $(SMOKE_EVALUATION_DIR)/dev_queries.jsonl \
		--naive-base-url $(DEV_NAIVE_BASE_URL) --stream-base-url $(DEV_STREAM_BASE_URL) \
		--require-distinct-services \
		--output $(DEV_PREDICTIONS)

score-dev:
	uv run python bench/score_dev.py --dev $(SMOKE_EVALUATION_DIR)/dev_queries.jsonl \
		--predictions $(DEV_PREDICTIONS) --output $(DEV_SUMMARY)

benchmark:
	uv run python bench/run_benchmark.py --warmup-repetitions 0 --repetitions 1 \
		--query-limit $(BENCH_QUERY_LIMIT) --wpm 70 \
		--case-timeout-s $(BENCH_CASE_TIMEOUT_S) --max-typing-drift-ms 100 \
		--queries $(BENCH_INFERENCE_DIR)/test_queries.jsonl \
		--naive-base-url $(NAIVE_BASE_URL) --stream-base-url $(STREAM_BASE_URL) \
		--output $(BENCH_PREDICTIONS)

score:
	uv run python bench/score.py --gold $(BENCH_EVALUATION_DIR)/test_gold.jsonl \
		--evaluation-manifest $(BENCH_EVALUATION_DIR)/checksums.sha256 \
		--predictions $(BENCH_PREDICTIONS) --output $(BENCH_SUMMARY)

score-final:
	uv run python bench/score.py --gold $(BENCH_EVALUATION_DIR)/test_gold.jsonl \
		--evaluation-manifest $(BENCH_EVALUATION_DIR)/checksums.sha256 \
		--predictions $(BENCH_PREDICTIONS) --output $(BENCH_SUMMARY) \
		--adjudications $(BENCH_ADJUDICATIONS) --require-manual-adjudication

docker-up:
	docker compose up --build

docker-down:
	docker compose down
