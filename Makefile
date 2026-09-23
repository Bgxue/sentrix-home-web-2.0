PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install lint format format-check typecheck arch test test-unit test-contract test-integration coverage check-docs web-check ci dev web-dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

# 前端依赖必须在这里装。只装 Python 依赖时，全新克隆跑 `make ci` 的 web-check 会在
# `npx tsc -b` 处去 npm 下载冒名包 tsc@2.0.4，打印 "This is not the tsc command you are
# looking for" —— 它确实 exit 1（不是静默通过），但报错指向的是 npx 而不是缺依赖，
# 后来人极易当成 npx 抖动而把前端检查删掉，于是又造出一个"看起来在检查、其实没有"。
# 用 `npm ci` 而不是 `npm install`：apps/web/package-lock.json 在版本控制里，
# `npm ci` 按 lock 装、lock 与 package.json 不一致时直接报错，比 install 可复现。
install: ## 建 venv 并安装全部依赖
	uv python install 3.11
	uv venv --python 3.11 --python-preference only-managed --clear --seed .venv
	$(PIP) install -q -e "apps/api[dev]"
	cd apps/web && npm ci

lint: ## ruff 检查
	.venv/bin/ruff check apps/api/src apps/api/tests scripts

format: ## ruff 自动修复
	.venv/bin/ruff check --fix apps/api/src apps/api/tests scripts
	.venv/bin/ruff format apps/api/src apps/api/tests scripts

format-check: ## ruff 格式检查（只读）
	.venv/bin/ruff format --check apps/api/src apps/api/tests scripts

typecheck: ## mypy strict（全包 + scripts）
	.venv/bin/mypy

# 两条互补的检查：lint-imports 管「谁不许 import 谁」，check_reachability 管
# 「每个模块在生产装配路径上有没有调用者」。S4 两次交付「代码在、没人调」的能力
# （零调用的纯规则、零调用的 SummarizeEvent），两次都是事后被用户点出来的——
# 靠人记记不住，所以把它变成可机械判定的东西。
arch: ## 分层契约检查 + 生产可达性（已在 ci 链上）
	.venv/bin/lint-imports
	$(PY) scripts/check_reachability.py

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
# web-check 排在链尾：后端检查更快、失败信息更直接，前端构建最慢也最该放在最后。
ci: lint format-check typecheck arch check-docs coverage web-check ## CI 门禁
	@echo "ci: 全部通过"

# 前端门禁。类型检查必须用 `tsc -b`，不能用 `tsc --noEmit`：apps/web/tsconfig.json 是
# {"files": [], "references": [...]} 结构，普通 tsc 不遍历 project references，
# `npx tsc --noEmit --listFiles` 实测命中 0 个文件、恒 exit 0——它看起来在检查，其实永远通过。
# 三件事分开跑而不是 `npm run build`：类型 / lint / 构建的失败信息各自直接，
# 且 `npx vite build` 省掉 build 脚本里那次重复的 tsc -b。
# 第一条存在性断言是给"跳过 install / node_modules 被删"兜底的：此时 npx 会去下冒名包
# tsc@2.0.4 并打印 "This is not the tsc command you are looking for"，那句话不指向缺依赖。
# 断言先拦下来，只说该做什么。
web-check: ## 前端门禁：tsc -b + oxlint + vite build
	@test -d apps/web/node_modules || { echo "缺少 apps/web/node_modules，请先跑 make install"; exit 1; }
	cd apps/web && npx tsc -b
	cd apps/web && npx oxlint
	cd apps/web && npx vite build

dev: ## 起 2.0 API（8191）
	$(PY) -m uvicorn --factory sentrix.main:create_app --host 0.0.0.0 --port 8191 --app-dir apps/api/src

web-dev: ## 起 2.0 Web（4274）
	cd apps/web && npm run dev -- --port 4274
