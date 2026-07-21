/* ==========================================================================
   Hervorhebung der KPI-Kacheln -- clientseitig
   --------------------------------------------------------------------------
   Warum nicht in Python? Die Kette nach einem Kachelklick ist

       Klick -> kpi_click_to_filter -> build_filter_state -> render_table

   Jeder Pfeil ist eine eigene Server-Runde. Hing die Hervorhebung am Ende
   dieser Kette, wartete sie auf das Neurendern der DataTable -- der sichtbare
   Zustandswechsel kam spürbar verzögert.

   Diese Funktion hängt stattdessen direkt an den Filter-Steuerelementen und
   läuft im Browser: die Kachel schaltet um, sobald die erste Server-Runde
   zurück ist, unabhängig von der Tabelle.

   WICHTIG: Hier steht nur der Vergleich, NICHT die Regel. Welcher Filter zu
   welcher Kachel gehört, kommt aus kpi/kpi_rules.py und wird über den Store
   `store-kpi-filters` hereingereicht.
   ========================================================================== */
window.dash_clientside = window.dash_clientside || {};

(function () {
    "use strict";

    /** Mengenvergleich zweier Statuslisten (Reihenfolge egal). */
    function sameStatus(want, have) {
        if (want.length !== have.length) return false;
        var present = {};
        have.forEach(function (s) { present[s] = true; });
        return want.every(function (s) { return present[s] === true; });
    }

    window.dash_clientside.kpi = {
        /**
         * @param status      Wert von filter-status      (Array | null)
         * @param ohneKlass   Wert von filter-ohne-klass  (["on"] | [])
         * @param kpiFilters  KPI-ID -> {status, ohne_klass}, aus dem Store
         * @returns className je Kachel, in der Reihenfolge der Outputs
         */
        highlight: function (status, ohneKlass, kpiFilters) {
            var cbCtx = window.dash_clientside.callback_context;
            var outs = (cbCtx && cbCtx.outputs_list) || [];
            if (!outs.length) return window.dash_clientside.no_update;

            var cur = status || [];
            var curOhne = Boolean(ohneKlass && ohneKlass.length);
            var map = kpiFilters || {};

            var active = outs.map(function (o) {
                var f = map[o.id.kpi];
                if (!f) return false;
                return sameStatus(f.status || [], cur) &&
                       Boolean(f.ohne_klass) === curOhne;
            });

            // Keine Kachel aktiv -> alle im Normalzustand (nichts ausgrauen).
            if (!active.some(Boolean)) {
                return outs.map(function () { return "kpi-tile"; });
            }
            return active.map(function (a) {
                return a ? "kpi-tile kpi-tile--active" : "kpi-tile kpi-tile--muted";
            });
        },
    };
})();
