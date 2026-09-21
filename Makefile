PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install lint format typecheck arch test test-unit test-contract test-integration check-docs ci dev web-dev

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

typecheck: ## mypy strict（只查 domain + application）
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

# 已知状态：S1 Task 13–15 落地前既无 domain/application 代码也无测试，本目标预期为红
# （coverage 报 "No data was collected"，并以 fail_under=85 失败）——不是环境或配置问题
coverage: ## domain + application 覆盖率门禁（设计 §6.4：≥85%）
	.venv/bin/pytest --cov --cov-report=term-missing

check-docs: ## 文档防腐校验
	$(PY) scripts/check_capability_map.py
	$(PY) scripts/check_module_map.py

# 注意：所有目标必须从仓库根执行。从 apps/api/ 下跑 pytest 时 coverage 配置
# 不会被加载，fail_under=85 会静默失效（该目录下有独立的 pyproject.toml）。
ci: lint typecheck test check-docs arch coverage ## CI 门禁
	@echo "ci: 全部通过"

dev: ## 起 2.0 API（8191）
	$(PY) -m uvicorn sentrix.main:app --host 0.0.0.0 --port 8191 --app-dir apps/api/src

web-dev: ## 起 2.0 Web（4274）
	cd apps/web && npm run dev -- --port 4274
