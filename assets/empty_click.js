/* ==========================================================================
   "Klick ins Leere" -> KPI-Filter aufheben
   --------------------------------------------------------------------------
   Dash liefert bei `n_clicks` nur die Tatsache eines Klicks, nicht das
   getroffene DOM-Element. Ein n_clicks auf dem Tab-Container würde durch
   Event-Bubbling auch bei Klicks auf Kacheln oder Tabelle auslösen.

   Darum hier ein einziger Listener auf document-Ebene, der über
   `closest()` prüft, ob ein inhaltliches Element getroffen wurde. Ist das
   nicht der Fall, wird der Store `store-empty-click` gesetzt -- den Rest
   erledigt ein normaler Python-Callback (callbacks/filter_callbacks.py).

   `dash_clientside.set_props` (Dash >= 2.17) schreibt dabei direkt in eine
   Komponente, ohne dass ein clientside-Callback registriert werden muss.
   ========================================================================== */
(function () {
    "use strict";

    // Ein Klick INNERHALB eines dieser Bereiche gilt nicht als "ins Leere".
    var CONTENT = [
        ".kpi-tile",       // Kacheln haben ihre eigene Toggle-Logik
        ".table-card",     // Tabelle inkl. Toolbar, Sortierung, Pagination
        ".sidebar",        // beide Sidebars
        ".sidebar-overlay",
        ".team-header",
        ".app-footer",
        ".placeholder-card",
    ].join(",");

    document.addEventListener("click", function (ev) {
        var target = ev.target;
        if (!(target instanceof Element)) return;

        // Nur Klicks im Tab-Bereich berücksichtigen ...
        if (!target.closest(".app-main")) return;
        // ... und nur, wenn dabei kein Inhaltselement getroffen wurde.
        if (target.closest(CONTENT)) return;

        var api = window.dash_clientside;
        if (!api || typeof api.set_props !== "function") return;

        // Zeitstempel statt Zähler: bei jedem Klick garantiert ein neuer Wert,
        // ohne den vorherigen Stand kennen zu müssen.
        api.set_props("store-empty-click", { data: Date.now() });
    });
})();
