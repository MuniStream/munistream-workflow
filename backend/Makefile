# CivicStream Backend Makefile

.PHONY: help install dev test lint format clean build up down logs shell db-init

# Default target
help:
	@echo "CivicStream Backend Commands:"
	@echo "  install     Install dependencies"
	@echo "  dev         Run development server"
	@echo "  test        Run tests"
	@echo "  lint        Run linting (flake8, mypy)"
	@echo "  format      Format code (black)"
	@echo "  clean       Clean up cache files"
	@echo "  build       Build Docker images"
	@echo "  up          Start all services with Docker Compose"
	@echo "  down        Stop all services"
	@echo "  logs        View service logs"
	@echo "  shell       Open shell in backend container"
	@echo "  db-init     Initialize database with sample data"

# Development commands
install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v --cov=app --cov-report=html

lint:
	flake8 app/ tests/
	mypy app/

format:
	black app/ tests/
	isort app/ tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .mypy_cache/

# Docker commands
build:
	docker-compose build

up:
	docker-compose up -d

up-dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

down:
	docker-compose down

down-volumes:
	docker-compose down -v

logs:
	docker-compose logs -f backend

logs-all:
	docker-compose logs -f

shell:
	docker-compose exec backend /bin/bash

# Database commands
db-init:
	docker-compose exec backend python scripts/init_db.py

db-reset:
	docker-compose exec backend python scripts/init_db.py --reset

db-shell:
	docker-compose exec mongodb mongosh civicstream -u admin -p civicstream123

# Production commands
up-prod:
	docker-compose --profile production up -d

# Admin tools
admin-ui:
	docker-compose --profile admin up -d mongo-express

# Health check
health:
	curl -f http://localhost:8000/health || echo "Backend not healthy"

# API documentation
docs:
	@echo "API Documentation available at:"
	@echo "  Swagger UI: http://localhost:8000/docs"
	@echo "  ReDoc: http://localhost:8000/redoc"

# Complete setup for new developers
setup: install db-init
	@echo "Setup complete! Run 'make dev' to start development server."

# CI/CD targets
ci-test: lint test

ci-build: build

# Backup and restore
backup:
	docker-compose exec mongodb mongodump --uri="mongodb://admin:civicstream123@localhost:27017/civicstream?authSource=admin" --out=/data/backup

restore:
	docker-compose exec mongodb mongorestore --uri="mongodb://admin:civicstream123@localhost:27017/civicstream?authSource=admin" /data/backup/civicstream