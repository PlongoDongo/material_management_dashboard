"""
Einstiegspunkt.

    Entwicklung:  uvicorn data_api.main:app --reload --port 8000
    Produktion :  uvicorn data_api.main:app --host 0.0.0.0 --port 8000 --workers 4

Zu --workers: jeder Worker ist ein eigener Prozess mit eigenem Neo4j-Treiber,
eigenem SQL-Pool und eigenem In-Process-Cache. Das ist bei der Dimensionierung
der Datenbank-Pools zu beruecksichtigen (pool_size * workers) und der Grund,
warum der Cache spaeter nach Redis wandern sollte.
"""
from __future__ import annotations

from data_api.application import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("data_api.main:app", host="127.0.0.1", port=8000, reload=True)
