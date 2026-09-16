"""
Central configuration: colors, element IDs and KPI definitions.

Everything that is needed in more than one place (colors, IDs, KPI rules)
lives here in ONE spot. That way you avoid ID typos between layout and
callbacks, and you can adjust the look and feel centrally.
"""

# --------------------------------------------------------------------------
# Corporate colors (extracted from the mockup)
# --------------------------------------------------------------------------
COLORS = {
    "primary": "#1565c0",        # corporate blue (header, footer)
    "primary_dark": "#0d47a1",   # darker blue (hover, active tabs)
    "bg": "#eef1f4",             # page background
    "surface": "#ffffff",        # card/table background
    "border": "#dbe2ea",
    "text": "#1b2733",
    "text_muted": "#5b6b7d",
}

# KPI tile colors -- the order is also the display order
KPI_COLORS = {
    "green":  "#2e9e5b",   # Aktive Materialien
    "orange": "#ef6c00",   # Nicht gelieferte Teile
    "slate":  "#6b7683",   # Obsolete Materialien
    "red":    "#d13b3b",   # Gesperrte Materialien
    "purple": "#6a4bc0",   # Ohne Klassifizierung
}

# Status values + their dot colors in the table
STATUS_COLORS = {
    "Aktiv":           "#2e9e5b",
    "Nicht geliefert": "#ef6c00",
    "Obsolet":         "#6b7683",
    "Gesperrt":        "#d13b3b",
}

# --------------------------------------------------------------------------
# Element IDs (one source of truth for layout AND callbacks)
# --------------------------------------------------------------------------
class IDS:
    # Header (reusable -- keep the IDs stable, then the header callbacks work
    # unchanged in every dashboard)
    MENU_BTN = "menu-btn"                 # burger on the left -> left nav sidebar
    FILTER_BTN = "filter-btn"             # filter icon on the right -> right filter sidebar

    # Sidebars
    NAV_SIDEBAR = "nav-sidebar"
    NAV_OVERLAY = "nav-overlay"
    NAV_CLOSE = "nav-close"
    FILTER_SIDEBAR = "filter-sidebar"
    FILTER_OVERLAY = "filter-overlay"
    FILTER_CLOSE = "filter-close"

    # Column selection (popover on the table button, replaces the former
    # inline "Filter" button -- that one belongs visually to the table)
    COLS_BTN = "columns-btn"              # opens the column popover
    COLS_MENU = "columns-menu"            # the popover panel itself
    COLS_CHECKLIST = "columns-checklist"  # selectable/deselectable columns
    COLS_ALL = "columns-all"              # show "Alle"
    COLS_NONE = "columns-none"            # "Keine" (only the pinned ones remain)

    # Filter controls (right sidebar) -- these are the filter's "sources of truth"
    F_STATUS = "filter-status"
    F_PLANT = "filter-plant"
    F_MATERIAL_GROUP = "filter-material-group"
    F_SEARCH = "filter-search"
    F_OHNE_KLASS = "filter-ohne-klass"
    F_RESET = "filter-reset"

    # Stores (central, persistent state)
    STORE_FILTERS = "store-filters"       # canonical filter state (session)
    STORE_ACTIVE_TAB = "store-active-tab" # active tab (session)
    STORE_EMPTY_CLICK = "store-empty-click"  # click on empty area (assets/empty_click.js)
    STORE_KPI_FILTERS = "store-kpi-filters"  # KPI rules for assets/kpi_highlight.js

    # Tabs in the footer
    TAB_OVERVIEW = "data-overview"
    TAB_MANAGE = "manage-data"
    TAB_MAPPINGS = "apply-data-mappings"

    # Content container per tab (always in the DOM, visibility via CSS)
    CONTENT_OVERVIEW = "content-overview"
    CONTENT_MANAGE = "content-manage"
    CONTENT_MAPPINGS = "content-mappings"

    # Data overview elements
    KPI_ROW = "kpi-row"
    TABLE = "material-table"
    RECORD_COUNTER = "record-counter"


# Order & labels of the footer tabs
TABS = [
    (IDS.TAB_OVERVIEW, "Data overview"),
    (IDS.TAB_MANAGE, "Manage data"),
    (IDS.TAB_MAPPINGS, "Apply data mappings"),
]

APP_TITLE = "Material Management"
APP_SUBTITLE = "Stammdaten-Cockpit"
APP_VERSION = "v0.1"

# --------------------------------------------------------------------------
# Header building blocks (central, so that the header only has to be adjusted
# here resp. via the function arguments of header_layout() per dashboard)
# --------------------------------------------------------------------------
RESTRICTION_TEXT = "Restricted"          # mini bar above the header
LOGO_SRC = "/assets/logo.svg"            # own logo: replace it here

# Note: the column schema (incl. FIXED_COLUMNS, labels, widths) is ONE source
# of truth in data/schema.py -- adjust it there, not here.
