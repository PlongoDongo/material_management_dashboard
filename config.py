"""
Zentrale Konfiguration: Farben, Element-IDs und KPI-Definitionen.

Alles, was an mehreren Stellen gebraucht wird (Farben, IDs, KPI-Regeln),
steht hier an EINER Stelle. So vermeidest du ID-Tippfehler zwischen Layout
und Callbacks und kannst das Look-and-Feel zentral anpassen.
"""

# --------------------------------------------------------------------------
# Unternehmensfarben (aus dem Mockup extrahiert)
# --------------------------------------------------------------------------
COLORS = {
    "primary": "#1565c0",        # Unternehmensblau (Header, Footer)
    "primary_dark": "#0d47a1",   # dunkleres Blau (Hover, aktive Tabs)
    "bg": "#eef1f4",             # Seitenhintergrund
    "surface": "#ffffff",        # Karten-/Tabellenhintergrund
    "border": "#dbe2ea",
    "text": "#1b2733",
    "text_muted": "#5b6b7d",
}

# KPI-Kachelfarben (Reihenfolge = Anzeige-Reihenfolge)
KPI_COLORS = {
    "green":  "#2e9e5b",   # Aktive Materialien
    "orange": "#ef6c00",   # Nicht gelieferte Teile
    "slate":  "#6b7683",   # Obsolete Materialien
    "red":    "#d13b3b",   # Gesperrte Materialien
    "purple": "#6a4bc0",   # Ohne Klassifizierung
}

# Statuswerte + zugehörige Punktfarben in der Tabelle
STATUS_COLORS = {
    "Aktiv":           "#2e9e5b",
    "Nicht geliefert": "#ef6c00",
    "Obsolet":         "#6b7683",
    "Gesperrt":        "#d13b3b",
}

# --------------------------------------------------------------------------
# Element-IDs (eine Wahrheit für Layout UND Callbacks)
# --------------------------------------------------------------------------
class IDS:
    # Header (wiederverwendbar -- IDs stabil halten, dann greifen die
    # Header-Callbacks in jedem Dashboard unverändert)
    MENU_BTN = "menu-btn"                 # Burger links  -> linke Nav-Sidebar
    FILTER_BTN = "filter-btn"             # Filter-Icon rechts -> rechte Filter-Sidebar

    # Sidebars
    NAV_SIDEBAR = "nav-sidebar"
    NAV_OVERLAY = "nav-overlay"
    NAV_CLOSE = "nav-close"
    FILTER_SIDEBAR = "filter-sidebar"
    FILTER_OVERLAY = "filter-overlay"
    FILTER_CLOSE = "filter-close"

    # Spaltenauswahl (Popover am Tabellen-Button, ersetzt den früheren
    # Inline-"Filter"-Button -- der gehört visuell zur Tabelle)
    COLS_BTN = "columns-btn"              # öffnet das Spalten-Popover
    COLS_MENU = "columns-menu"            # das Popover-Panel selbst
    COLS_CHECKLIST = "columns-checklist"  # an-/abwählbare Spalten
    COLS_ALL = "columns-all"              # "Alle" einblenden
    COLS_NONE = "columns-none"            # "Keine" (nur die fixierten bleiben)

    # Filtersteuerung (rechte Sidebar) -- das sind die "Wahrheitsquellen" des Filters
    F_STATUS = "filter-status"
    F_WERK = "filter-werk"
    F_WARENGRUPPE = "filter-warengruppe"
    F_SEARCH = "filter-search"
    F_OHNE_KLASS = "filter-ohne-klass"
    F_RESET = "filter-reset"

    # Stores (zentraler, persistenter State)
    STORE_FILTERS = "store-filters"       # kanonischer Filterzustand (session)
    STORE_ACTIVE_TAB = "store-active-tab" # aktiver Tab (session)
    STORE_EMPTY_CLICK = "store-empty-click"  # Klick auf leere Fläche (assets/empty_click.js)
    STORE_KPI_FILTERS = "store-kpi-filters"  # KPI-Regeln für assets/kpi_highlight.js

    # Tabs (Footer)
    TAB_OVERVIEW = "data-overview"
    TAB_MANAGE = "manage-data"
    TAB_MAPPINGS = "apply-data-mappings"

    # Inhaltscontainer je Tab (immer im DOM, Sichtbarkeit per CSS)
    CONTENT_OVERVIEW = "content-overview"
    CONTENT_MANAGE = "content-manage"
    CONTENT_MAPPINGS = "content-mappings"

    # Data-Overview-Elemente
    KPI_ROW = "kpi-row"
    TABLE = "material-table"
    RECORD_COUNTER = "record-counter"


# Reihenfolge & Beschriftung der Footer-Tabs
TABS = [
    (IDS.TAB_OVERVIEW, "Data overview"),
    (IDS.TAB_MANAGE, "Manage data"),
    (IDS.TAB_MAPPINGS, "Apply data mappings"),
]

APP_TITLE = "Material Management"
APP_SUBTITLE = "Stammdaten-Cockpit"
APP_VERSION = "v0.1"

# --------------------------------------------------------------------------
# Header-Bausteine (zentral, damit der Header pro Dashboard nur hier bzw. über
# die Funktionsargumente von header_layout() angepasst werden muss)
# --------------------------------------------------------------------------
RESTRICTION_TEXT = "Restricted"          # Mini-Leiste über dem Header
LOGO_SRC = "/assets/logo.svg"            # eigenes Logo: hier ersetzen

# Spalten, die in der Materialtabelle IMMER sichtbar und beim horizontalen
# Scrollen links fixiert bleiben. Alle übrigen Spalten sind über das
# Spalten-Popover an-/abwählbar.
FIXED_COLUMNS = ("material_nr", "bezeichnung")
