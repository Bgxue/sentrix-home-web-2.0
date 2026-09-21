PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install lint format format-check typecheck arch test test-unit test-contract test-integration check-docs ci ci-full dev web-dev

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

# 已知状态：S1 Task 13–15 落地前既无 domain/application 代码也无测试，本目标预期为红
# （coverage 报 "No data was collected"，并以 fail_under=85 失败）——不是环境或配置问题
coverage: ## domain + application 覆盖率门禁（设计 §6.4：≥85%）
	.venv/bin/pytest --cov --cov-report=term-missing

check-docs: ## 文档防腐校验
	$(PY) scripts/check_capability_map.py
	$(PY) scripts/check_module_map.py

# 注意：所有目标必须从仓库根执行。从 apps/api/ 下跑 pytest 时 coverage 配置
# 不会被加载，fail_under=85 会静默失效（该目录下有独立的 pyproject.toml）。
ci: lint format-check typecheck arch test check-docs ## CI 门禁（coverage 见 ci-full）
	@echo "ci: 全部通过"

# 一个长期恒红的 ci 会被所有人学会无视，所以把 coverage 拆到这里。
# S1 Task 13 合入（domain/application 有代码）时把两者并回一个 ci。
ci-full: ci coverage ## 含覆盖率门禁（当前预期为红）
	@echo "ci-full: 全部通过"

dev: ## 起 2.0 API（8191）
	$(PY) -m uvicorn --factory sentrix.main:create_app --host 0.0.0.0 --port 8191 --app-dir apps/api/src

web-dev: ## 起 2.0 Web（4274）
	cd apps/web && npm run dev -- --port 4274
