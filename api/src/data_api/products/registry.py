"""
The registry: the directory of all data products.

At its core a dictionary. The key is (name, major version), the value is the
data product:

    {("material-overview", 2): DataProduct(...),
     ("material-overview", 3): DataProduct(...),
     ("supplier-risk",     2): DataProduct(...)}

Adding a data product means: create ONE file in products/catalog/ that ends with
`registry.add(DataProduct(...))`. No router, no import list, no if-cascade.

How the file is found: at startup `discover()` looks at which files live in the
catalog directory and imports them. The import runs `registry.add(...)` and the
product is in the directory. After that products/router.py builds the routes
from it.
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
        """Registers a data product. Called at the end of every catalog file."""
        key = (product.name, product.major)
        if key in self._products:
            existing = self._products[key]
            raise ValueError(
                f"Data product collision: '{product.name}' {product.path_version} is "
                f"already registered as version {existing.version}. Breaking change? "
                f"Then bump the MAJOR."
            )
        self._products[key] = product
        log.debug("Data product registered: %s %s", product.name, product.version)

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
        """Newest non-deprecated version; if all are deprecated, the highest."""
        versions = self.versions_of(name)
        if not versions:
            return None
        current = [p for p in versions if not p.deprecated]
        return (current or versions)[-1]

    def __len__(self) -> int:
        return len(self._products)


# Process-wide registry. The catalog files fill it on import.
registry = ProductRegistry()


def discover(package: str = "data_api.products.catalog") -> int:
    """Imports every catalog file -- which is how they register themselves.

    Instead of an import list somebody would have to maintain, the directory is
    read. Dropping in a file is enough.

    An import error in a catalog file aborts startup. That is intentional: a
    half-loaded catalog would be worse than a hard failure, because a route
    would simply be missing without anyone noticing.
    """
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
    log.info("Data product catalog loaded: %d products.", len(registry))
    return len(registry)
