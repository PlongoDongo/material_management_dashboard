"""
Registry: das zentrale Verzeichnis aller Datenprodukte.

Ein neues Datenprodukt anzubinden heisst: EINE Datei in products/catalog/
anlegen. Kein Router anfassen, keine main.py anfassen, keine if-Kaskade
erweitern. Genau das ist mit "leicht erweiterbar" gemeint.

    @data_product(name="material-overview", version="1.0", item_model=Row, ...)
    async def load(repos, params): ...

Die Registry prueft beim Start auf Kollisionen (zwei Produkte mit gleichem
Namen und gleichem Major) -- lieber ein Startfehler als zwei Routen, von denen
zufaellig eine gewinnt.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable

from data_api.products.base import DataProduct, Loader, ProductParams

log = logging.getLogger(__name__)


class ProductRegistry:
    def __init__(self) -> None:
        self._products: dict[tuple[str, int], DataProduct] = {}

    def add(self, product: DataProduct) -> None:
        if product.key in self._products:
            existing = self._products[product.key]
            raise ValueError(
                f"Datenprodukt-Kollision: '{product.name}' {product.path_version} ist "
                f"bereits als Version {existing.version} registriert. Brechende Aenderung? "
                f"Dann MAJOR erhoehen."
            )
        self._products[product.key] = product
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
        """Neueste NICHT-deprecated Version; falls alle deprecated: die hoechste."""
        versions = self.versions_of(name)
        if not versions:
            return None
        live = [p for p in versions if not p.deprecated]
        return (live or versions)[-1]

    def __len__(self) -> int:
        return len(self._products)


# Prozessweite Registry. Die Kataloge fuellen sie beim Import.
registry = ProductRegistry()


def data_product(
    *,
    name: str,
    version: str,
    summary: str,
    item_model: type,
    params_model: type[ProductParams] = ProductParams,
    owner: str = "unassigned",
    description: str = "",
    tags: Iterable[str] = (),
    cache_ttl: int = 60,
    deprecated: bool = False,
    sunset: object = None,
    required_groups: Iterable[str] = (),
) -> Callable[[Loader], Loader]:
    """Dekorator: macht aus einer Loader-Funktion ein registriertes Datenprodukt."""

    def wrap(loader: Loader) -> Loader:
        registry.add(
            DataProduct(
                name=name,
                version=version,
                summary=summary,
                item_model=item_model,
                loader=loader,
                params_model=params_model,
                owner=owner,
                description=description or (loader.__doc__ or "").strip(),
                tags=tuple(tags),
                cache_ttl=cache_ttl,
                deprecated=deprecated,
                sunset=sunset,
                required_groups=frozenset(required_groups),
            )
        )
        return loader

    return wrap


def discover(package: str = "data_api.products.catalog") -> int:
    """Importiert alle Katalogmodule -> deren Dekoratoren registrieren sich.

    Auto-Discovery statt einer handgepflegten Importliste: eine Datei ins
    Katalogverzeichnis legen genuegt. Der Preis ist, dass ein Importfehler in
    einem Katalogmodul den Start abbricht -- das ist hier erwuenscht (ein halb
    geladener Katalog waere schlimmer als ein harter Fehler).
    """
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
    log.info("Datenprodukt-Katalog geladen: %d Produkte.", len(registry))
    return len(registry)
