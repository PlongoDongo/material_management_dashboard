"""
Der Datenprodukt-Katalog.

Jede Datei hier = ein Datenprodukt in genau einer Version. Sie wird beim Start
automatisch importiert (products/registry.py::discover), der Dekorator meldet
das Produkt an, der Router erzeugt die Route. Es gibt keine weitere Stelle,
an der ein neues Produkt eingetragen werden muss.

Namenskonvention: <produkt_name>_v<major>.py -- so sieht man im Verzeichnis
sofort, welche Versionen live sind.

Aufbau jeder Datei, immer gleich (das ist Absicht -- Vorhersagbarkeit schlaegt
Eleganz, wenn mehrere Teams Produkte beisteuern):

    1. Row-Modell   -> der Vertrag
    2. Params       -> erlaubte Filter
    3. transform()  -> REINE Funktion: Rohzeilen + Params -> Produktzeilen
    4. load()       -> holt Rohdaten aus Repositories, ruft transform()
"""
