# short-tale — common operations
.PHONY: help setup up down logs build doctor models login run dry test scan clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## First-time setup: create .env and required folders
	@test -f .env || (cp .env.example .env && echo "created .env — fill in your Reddit app credentials")
	@mkdir -p data out assets/broll assets/fonts assets/brand
	@echo "next: edit .env, then 'make up' and 'make models'"

build:  ## Build the container images
	docker compose build

up:     ## Start the whole stack
	docker compose up -d
	@echo "review UI -> http://localhost:$${APP_PORT:-8080}"

down:   ## Stop the stack
	docker compose down

logs:   ## Tail logs from every service
	docker compose logs -f --tail=100

doctor: ## Check GPU, NVENC, models, and config without running a job
	docker compose run --rm worker shorttale doctor

models: ## Download the LLM + speech + caption models (one time, ~12GB)
	./scripts/bootstrap_models.sh

login:  ## Open the browser to sign in to YouTube by hand (one time)
	@echo "Open http://localhost:$${NOVNC_PORT:-7900}/vnc.html and sign in."
	docker compose exec publisher python3 -m publisher.login

run:    ## Generate one video now for a campaign: make run C=tailmailer
	docker compose exec worker shorttale run --campaign $(C)

dry:    ## Full offline dry run — no network, no GPU, renders a sample video
	docker compose run --rm worker shorttale run --campaign demo --dry-run

test:   ## Run the test suite
	docker compose run --rm worker python3 -m pytest -q

scan:   ## Verify no secrets are staged for commit
	./scripts/check_secrets.sh

clean:  ## Remove generated media and work dirs (keeps models and profile)
	rm -rf out/* data/work/*
