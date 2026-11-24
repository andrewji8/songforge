.PHONY: install test run-api run-worker frontend docker-up

install:
	pip install -r requirements.txt -r requirements-dev.txt

test:
	python -m pytest backend/tests -q

run-api:
	cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm install && npm run dev

docker-up:
	docker compose up --build
