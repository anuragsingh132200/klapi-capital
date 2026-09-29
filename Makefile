.PHONY: install test run docker

install:
	python -m pip install -r requirements-dev.txt

test:
	python -m pytest -q

run:
	uvicorn app.main:app --reload

docker:
	docker compose up --build
