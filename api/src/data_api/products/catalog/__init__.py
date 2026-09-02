"""
Der Datenprodukt-Katalog.

Jede Datei hier = ein Datenprodukt in genau einer Hauptversion. Sie wird beim
Start automatisch importiert (products/registry.py::discover), traegt sich
selbst ein, und der Router baut daraus eine Route.

Namenskonvention: <produkt_name>_v<major>.py

Aufbau jeder Datei -- immer in dieser Reihenfolge. Vorhersagbarkeit ist hier
wichtiger als Eleganz, weil mehrere Teams Produkte beisteuern:

    1. CYPHER / SQL   die Abfrage
    2. Row-Modell     der Vertrag: welche Felder kommen raus
    3. Params-Modell  die erlaubten Filter
    4. transform()    REINE Funktion: Rohzeilen + Params -> Produktzeilen
    5. load()         holt die Rohzeilen, ruft transform()
    6. registry.add() veroeffentlicht das Produkt

Schritt 4 ist der wichtige: `transform()` sieht weder Datenbank noch HTTP und
ist deshalb in Millisekunden testbar. Dort liegt die Fachlichkeit, dort liegen
die Fehler, dort liegen die meisten Tests.
"""
