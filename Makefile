.PHONY: up down psql download-historic load lab

up:
	docker compose up -d

down:
	docker compose down

psql:
	docker compose exec db psql -U $${POSTGRES_USER:-transit} -d $${POSTGRES_DB:-bay_transit}

# make download-historic MONTH=2025-08
download-historic:
	python scripts/download_gtfs.py --historic $(MONTH) --with-observations

# make load PATH=data/raw/RG_2025-08_so
load:
	python scripts/load_gtfs.py --path $(PATH) --init-schema

lab:
	jupyter lab notebooks/
