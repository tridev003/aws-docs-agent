.PHONY: install ingest run test lint fmt docker-build docker-run tf-init tf-plan tf-apply tf-destroy push-image clean help

PROJECT  := aws-docs-agent
PY       := python3
REGION   ?= us-east-1
ACCOUNT  := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
ECR_URI  := $(ACCOUNT).dkr.ecr.$(REGION).amazonaws.com/$(PROJECT)

help:
	@echo "Targets:"
	@echo "  install        Create venv and install package (editable)"
	@echo "  ingest         Run the doc ingestion pipeline locally"
	@echo "  run            Launch the Streamlit UI locally"
	@echo "  test           Run unit tests"
	@echo "  lint / fmt     Ruff lint / format"
	@echo "  docker-build   Build the container image"
	@echo "  docker-run     Run the container locally"
	@echo "  push-image     Build, tag, and push image to ECR"
	@echo "  tf-init        terraform init in infra/"
	@echo "  tf-plan        terraform plan in infra/"
	@echo "  tf-apply       terraform apply in infra/"
	@echo "  tf-destroy     terraform destroy in infra/"

install:
	$(PY) -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -e ".[dev]"

ingest:
	. .venv/bin/activate && $(PY) -m aws_docs_agent.rag.ingest

run:
	. .venv/bin/activate && streamlit run src/aws_docs_agent/ui/streamlit_app.py

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check src tests

fmt:
	. .venv/bin/activate && ruff format src tests

docker-build:
	docker build -t $(PROJECT):local -f docker/Dockerfile .

docker-run:
	docker run --rm -it -p 8501:8501 \
		-e AWS_REGION=$(REGION) \
		-v $$HOME/.aws:/root/.aws:ro \
		-v $$PWD/data:/app/data \
		$(PROJECT):local

push-image:
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin $(ECR_URI)
	docker build --platform linux/amd64 -t $(PROJECT):latest -f docker/Dockerfile .
	docker tag  $(PROJECT):latest $(ECR_URI):latest
	docker push $(ECR_URI):latest

tf-init:
	cd infra && terraform init

tf-plan:
	cd infra && terraform plan

tf-apply:
	cd infra && terraform apply

tf-destroy:
	cd infra && terraform destroy

clean:
	rm -rf .venv .pytest_cache .ruff_cache build dist *.egg-info
