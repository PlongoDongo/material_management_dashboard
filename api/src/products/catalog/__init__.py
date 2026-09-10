"""
The data product catalog.

Every file here is one data product in exactly one major version. It is imported
automatically at startup (products/registry.py::discover), registers itself, and
the router turns it into a route.

Naming convention: <product_name>_v<major>.py

Structure of every file -- always in this order. Predictability matters more
than elegance here, because several teams contribute products:

    1. CYPHER / SQL   the query
    2. row model      the contract: which fields come out
    3. params model   the allowed filters
    4. transform()    PURE function: raw rows + params -> product rows
    5. load()         fetches the raw rows, calls transform()
    6. registry.add() publishes the product

Step 4 is the important one: `transform()` sees neither a database nor HTTP and
is therefore testable in milliseconds. That is where the domain logic lives,
that is where the bugs live, and that is where most of the tests live.

A note on field names: the query aliases (`RETURN m.nr AS material_number`) are
where the graph's own vocabulary meets the API contract. The graph properties
belong to the data model and keep their names; the aliases are English, like the
rest of the code.
"""
