"""
Die Registry: das Verzeichnis aller Datenprodukte.

Im Kern ein Woerterbuch. Schluessel ist (Name, Hauptversion), Wert das
Datenprodukt:

    {("material-overview", 1): DataProduct(...),
     ("material-overview", 2): DataProduct(...),
     ("supplier-risk",     1): DataProduct(...)}

Ein neues Datenprodukt anzubinden heisst: EINE Datei in products/catalog/
anlegen, die am Ende `registry.add(DataProduct(...))` aufruft. Kein Router,
keine Importliste, keine if-Kaskade.

Wie die Datei gefunden wird: `discover()` schaut beim Start nach, welche
Dateien im Katalogordner liegen, und importiert sie. Beim Import laeuft
`registry.add(...)` und das Produkt steht im Verzeichnis. Danach baut
products/router.py daraus die Routen.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

from data_api.products.base import DataProduct

log = logging.getLogger(__name__)


class ProductRegistry:
    def __init__(self) -> None:
        self._products: dict[tuple[str, int], DataProduct] = {}

    def add(self, product: DataProduct) -> None:
        """Traegt ein Datenprodukt ein. Wird am Ende jeder Katalogdatei aufgerufen."""
        schluessel = (product.name, product.major)
        if schluessel in self._products:
            vorhanden = self._products[schluessel]
            raise ValueError(
                f"Datenprodukt-Kollision: '{product.name}' {product.path_version} ist "
                f"bereits als Version {vorhanden.version} registriert. Brechende "
                f"Aenderung? Dann MAJOR erhoehen."
            )
        self._products[schluessel] = product
        log.debug("Datenprodukt registriert: %s %s", product.name, product.version)

    def all(self) -> list[DataProduct]:
        return sorted(self._products.values(), key=lambda p: (p.name, p.major))

    def names(self) -> list[str]:
        return sorted({p.name for p in self._products.values()})

    def versions_of(self, name: str) -> list[DataProduct]:
        return sorted((p for p in self._products.values() if p.name == name),
                      key=lambda p: p.major)

    def get(self, name: str, major: int) -> DataProduct | None:
        return self._products.get((name, major))

    def latest(self, name: str) -> DataProduct | None:
        """Neueste nicht-abgekuendigte Version; sind alle abgekuendigt, die hoechste."""
        versionen = self.versions_of(name)
        if not versionen:
            return None
        aktuell = [p for p in versionen if not p.deprecated]
        return (aktuell or versionen)[-1]

    def __len__(self) -> int:
        return len(self._products)


# Prozessweite Registry. Die Katalogdateien fuellen sie beim Import.
registry = ProductRegistry()


def discover(package: str = "data_api.products.catalog") -> int:
    """Importiert alle Katalogdateien -- dadurch registrieren sie sich.

    Statt einer Importliste, die jemand pflegen muesste, wird das Verzeichnis
    gelesen. Eine Datei ablegen genuegt.

    Ein Importfehler in einer Katalogdatei bricht den Start ab. Das ist gewollt:
    ein halb geladener Katalog waere schlimmer als ein harter Fehler, weil eine
    Route dann einfach fehlt, ohne dass es jemandem auffaellt.
    """
    modul = importlib.import_module(package)
    for info in pkgutil.iter_modules(modul.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
    log.info("Datenprodukt-Katalog geladen: %d Produkte.", len(registry))
    return len(registry)
