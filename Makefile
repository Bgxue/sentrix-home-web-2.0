PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install lint format format-check typecheck arch test test-unit test-contract test-integration coverage check-docs ci dev web-dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

install: ## 建 venv 并安装全部依赖
	uv python install 3.11
	uv venv --python 3.11 --python-preference only-managed --clear --seed .venv
	$(PIP) install -q -e "apps/api[dev]"

lint: ## ruff 检查
	.venv/bin/ruff check apps/api/src apps/api/tests scripts

format: ## ruff 自动修复
	.venv/bin/ruff check --fix apps/api/src apps/api/tests scripts
	.venv/bin/ruff format apps/api/src apps/api/tests scripts

format-check: ## ruff 格式检查（只读）
	.venv/bin/ruff format --check apps/api/src apps/api/tests scripts

typecheck: ## mypy strict（全包 + scripts）
	.venv/bin/mypy

arch: ## 分层契约检查（已在 ci 链上）
	.venv/bin/lint-imports

test-unit: ## 单元测试
	.venv/bin/pytest apps/api/tests/unit -q

test-contract: ## 契约测试
	.venv/bin/pytest apps/api/tests/contract -q

test-integration: ## 集成测试
	.venv/bin/pytest apps/api/tests/integration -q

test: ## 全部测试
	.venv/bin/pytest -q

# S1 Task 13 已合入：domain/application 有代码，本目标实测 95.6%（阈值 85%），门禁为绿。
coverage: ## domain + application 覆盖率门禁（设计 §6.4：≥85%）
	.venv/bin/pytest --cov --cov-report=term-missing

check-docs: ## 文档防腐校验
	$(PY) scripts/check_capability_map.py
	$(PY) scripts/check_module_map.py
	$(PY) scripts/export_openapi.py --check

# 注意：所有目标必须从仓库根执行。从 apps/api/ 下跑 pytest 时 coverage 配置
# 不会被加载，fail_under=85 会静默失效（该目录下有独立的 pyproject.toml）。
# coverage 本身跑的就是全量测试，所以链上不再单列 test——同一套测试不必跑两遍。
ci: lint format-check typecheck arch check-docs coverage ## CI 门禁
	@echo "ci: 全部通过"

dev: ## 起 2.0 API（8191）
	$(PY) -m uvicorn --factory sentrix.main:create_app --host 0.0.0.0 --port 8191 --app-dir apps/api/src

web-dev: ## 起 2.0 Web（4274）
	cd apps/web && npm run dev -- --port 4274
