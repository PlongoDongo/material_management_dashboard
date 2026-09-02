"""
Tests der Betriebsschicht.

Diese Datei deckt Fehler ab, die alle dasselbe Muster hatten: Sie warfen keine
Ausnahme, sie beantworteten nur eine andere Frage als die gestellte. Genau die
Klasse, gegen die der Rest des Projekts argumentiert (`extra="forbid"`, die
Typuebersetzung, der LIMIT-Abschnitt im Leitfaden) -- die Betriebsschicht war
nur noch nicht nach demselben Massstab durchgegangen.
"""
from __future__ import annotations

import datetime as dt
import locale

import pytest
from fastapi.testclient import TestClient

from data_api.application import create_app
from data_api.core.config import Settings
from data_api.core.security import ANONYMOUS, Principal


# --- .env.example ist ausgelieferte Schnittstelle ---------------------------

def test_env_example_ist_ladbar_und_laesst_auth_aus(tmp_path):
    """`cp .env.example .env` muss zu einer startbaren App fuehren.

    Zwei Fallen auf einmal: pydantic-settings liest komplexe Felder (list[str])
    in der Quelle als JSON -- ohne NoDecode scheitert der Start an
    `API_CORS_ORIGINS=a,b`. Und python-dotenv entfernt einen nachgestellten
    Kommentar nur, wenn ein Wert davorsteht: `API_KEYS=  # leer = aus` haette
    den Kommentartext als Schluessel gelesen und die Auth EINgeschaltet.
    """
    from pathlib import Path

    beispiel = Path(__file__).resolve().parents[1] / ".env.example"
    ziel = tmp_path / ".env"
    ziel.write_text(beispiel.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=str(ziel))
    assert settings.api_cors_origins == ["http://localhost:8050", "http://localhost:8051"]
    assert settings.api_keys == []
    assert settings.auth_enabled is False


# --- Request-ID -------------------------------------------------------------

def test_request_id_steht_in_der_access_logzeile(client, caplog):
    """Die eine Zeile, die Pfad, Status und Dauer zusammenbringt, braucht die ID.

    Vorher wurde der ContextVar im `finally` zurueckgesetzt -- also VOR dem
    Log-Aufruf -- und ausgerechnet diese Zeile trug "-".
    """
    with caplog.at_level("INFO", logger="data_api.core.middleware"):
        client.get("/api/v1/healthz", headers={"X-Request-ID": "abc123"})

    zeilen = [r for r in caplog.records if "healthz" in r.getMessage()]
    assert zeilen, "keine Access-Logzeile gefunden"
    assert zeilen[-1].request_id == "abc123"


def test_request_id_ueberlebt_einen_serverfehler(settings):
    """Bei 500 kommt die Antwort nicht mehr durch die Middleware.

    Genau dort ist die Korrelation am meisten wert -- also muss die ID sowohl im
    Body als auch im Header stehen.
    """
    app = create_app(settings)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("absichtlich")

    with TestClient(app, raise_server_exceptions=False) as client:
        antwort = client.get("/boom", headers={"X-Request-ID": "abc123"})

    assert antwort.status_code == 500
    assert antwort.json()["request_id"] == "abc123"
    assert antwort.headers["X-Request-ID"] == "abc123"


# --- Umschlag-Metadaten -----------------------------------------------------

def test_meta_source_bleibt_bei_cache_treffern_korrekt(client):
    """Beim Cache-Treffer laeuft keine Abfrage -- die Herkunft muss trotzdem stimmen.

    Vorher meldete jede zwischengespeicherte Antwort `source="none"`. Bei
    cache_ttl=300 auf supplier-risk war das die Mehrzahl aller Antworten.
    """
    pfad = "/api/v1/data-products/supplier-risk/v1"
    erste = client.get(pfad).json()["meta"]
    zweite = client.get(pfad).json()["meta"]

    assert erste["cache"] == "miss" and zweite["cache"] == "hit"
    assert zweite["source"] == erste["source"] == "neo4j+postgres"


def test_generated_at_meint_den_zeitpunkt_der_abfrage(client):
    """Nicht "jetzt" -- sonst waere das Altersfeld bei jedem Treffer frisch."""
    pfad = "/api/v1/data-products/supplier-risk/v1"
    erste = client.get(pfad).json()["meta"]["generated_at"]
    zweite = client.get(pfad).json()["meta"]["generated_at"]
    assert erste == zweite


def test_blaettern_loest_keinen_neuen_datenbanklauf_aus(client, fake_sources):
    """limit/offset waehlen einen Ausschnitt, sie bestimmen nicht den Datensatz.

    Vorher steckten sie im Cache-Schluessel: jede Seite war ein voller Neulauf
    des Loaders, und derselbe Datensatz lag N-mal im Cache.
    """
    pfad = "/api/v1/data-products/material-overview/v2"
    client.get(pfad, params={"limit": 20, "offset": 0})
    anzahl_nach_seite_1 = len(fake_sources.aufrufe)

    for offset in (20, 40):
        antwort = client.get(pfad, params={"limit": 20, "offset": offset})
        assert antwort.json()["meta"]["cache"] == "hit"
        assert antwort.json()["meta"]["total_count"] == 64

    assert len(fake_sources.aufrufe) == anzahl_nach_seite_1


# --- Sunset-Header ----------------------------------------------------------

def test_sunset_header_ist_locale_unabhaengig(client):
    """RFC 9110 verlangt ein englisches, festes Datumsformat.

    `strftime("%a, %d %b ...")` folgt der Locale des Containers und lieferte
    unter LANG=de_DE "Do., 31 Dez. 2026" -- unparsebar fuer jeden Client.
    """
    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except locale.Error:
        pytest.skip("Locale de_DE.UTF-8 nicht installiert")
    try:
        antwort = client.get("/api/v1/data-products/material-overview/v1")
        assert antwort.headers["Sunset"] == "Thu, 31 Dec 2026 00:00:00 GMT"
    finally:
        locale.setlocale(locale.LC_TIME, "C")


# --- Authentifizierung ------------------------------------------------------

def test_katalog_ist_genauso_geschuetzt_wie_die_datenprodukte(settings):
    """Der Katalog listet Owner, Cache-Zeiten und alle Vertragsfelder.

    Ihn ohne Schluessel offen zu lassen waere eine Entscheidung -- vorher war es
    nur eine ausgelassene Zeile.
    """
    geschuetzt = settings.model_copy(update={"api_keys": ["geheim"]})
    with TestClient(create_app(geschuetzt)) as client:
        assert client.get("/api/v1/catalog").status_code == 401
        assert client.get("/api/v1/catalog/material-overview").status_code == 401
        assert client.get("/api/v1/catalog",
                          headers={"X-API-Key": "geheim"}).status_code == 200


def test_ausgeschaltete_auth_sperrt_nicht_aus():
    """"Auth aus" muss ALLES offen heissen, nicht "nur die Gruppe public".

    Vorher war die Entwicklung strenger als die Produktion: ein Produkt mit
    required_groups=("internal",) antwortete lokal mit 403, obwohl Auth aus war.
    """
    assert ANONYMOUS.darf(("internal",)) is True
    assert ANONYMOUS.darf(()) is True

    angemeldet = Principal(subject="x", groups=frozenset({"public"}), auth_aktiv=True)
    assert angemeldet.darf(("internal",)) is False
    assert angemeldet.darf(("public",)) is True


def test_api_schluessel_taucht_nicht_in_der_antwort_auf(settings):
    """`geaendert_von` gehoert eine Identitaet, kein Anmeldedatum.

    Vorher standen die ersten vier Zeichen des Schluessels in der Antwort und in
    jeder Logzeile.
    """
    geschuetzt = settings.model_copy(update={"api_keys": ["superlanges-geheimnis"]})
    with TestClient(create_app(geschuetzt)) as client:
        antwort = client.post(
            "/api/v1/mappings",
            headers={"X-API-Key": "superlanges-geheimnis"},
            json={"material_nr": "MAT-1", "ziel_warengruppe": "Rohstoffe"},
        )
    assert antwort.status_code == 201
    subject = antwort.json()["geaendert_von"]
    assert subject.startswith("apikey:")
    assert "supe" not in subject           # keine Zeichen des Schluessels


# --- Readiness --------------------------------------------------------------

def test_readyz_prueft_nur_benoetigte_quellen(client_ohne_datenquellen):
    """Beide Quellen werden hier gebraucht -> beide fehlen -> 503."""
    antwort = client_ohne_datenquellen.get("/api/v1/readyz")
    assert antwort.status_code == 503
    assert set(antwort.json()["required"]) == {"neo4j", "postgres"}


def test_benoetigte_quellen_werden_aus_den_loadern_gelesen():
    """Abgeleitet, nicht deklariert -- so kann es nicht auseinanderlaufen."""
    from data_api.products.introspect import required_sources, sources_used_by
    from data_api.products.catalog.material_overview_v2 import load as load_material
    from data_api.products.catalog.supplier_risk_v1 import load as load_risk

    assert sources_used_by(load_material) == ["neo4j"]
    assert sources_used_by(load_risk) == ["neo4j", "postgres"]
    assert required_sources() == {"neo4j", "postgres"}
