/* ==========================================================================
   Spaltenauswahl-Popover der Materialtabelle.

   Zwei Teile:
   1. Clientside-Callbacks (window.dash_clientside.cols) fuer die Logik:
      - applyVisibility: angehakte Spalten -> hidden_columns der DataTable
      - selectAll:       "Alle"/"Keine" -> Wert der Checkliste
   2. Ein Dokument-Listener, der das Panel oeffnet/schliesst (Klick auf den
      Button toggelt, Klick ausserhalb schliesst). Bewusst kein Dash-Callback,
      damit es sich ohne Server-Runde sofort anfuehlt.
   ========================================================================== */
window.dash_clientside = window.dash_clientside || {};

window.dash_clientside.cols = {
    // Angehakte Werte -> Liste der auszublendenden Spalten (die restlichen
    // toggelbaren). options kommt als [{label, value}, ...] herein.
    applyVisibility: function (values, options) {
        values = values || [];
        var all = (options || []).map(function (o) { return o.value; });
        return all.filter(function (v) { return values.indexOf(v) === -1; });
    },

    // "Alle" -> alle toggelbaren Werte, "Keine" -> leer. Welcher Button den
    // Callback ausgeloest hat, steht im callback_context.
    selectAll: function (nAll, nNone, options) {
        var all = (options || []).map(function (o) { return o.value; });
        var ctx = window.dash_clientside.callback_context;
        var trig = ctx && ctx.triggered && ctx.triggered.length
            ? ctx.triggered[0].prop_id : "";
        // prop_id sieht aus wie "columns-none.n_clicks"
        if (trig.indexOf("columns-none") === 0) {
            return [];
        }
        return all;
    },
};

// -- Oeffnen/Schliessen des Popovers ---------------------------------------
document.addEventListener("click", function (e) {
    var panel = document.getElementById("columns-menu");
    var btn = document.getElementById("columns-btn");
    if (!panel || !btn) { return; }

    if (btn.contains(e.target)) {
        // Klick auf den Button (oder sein Icon) -> auf/zu
        panel.classList.toggle("open");
        return;
    }
    if (!panel.contains(e.target)) {
        // Klick irgendwo ausserhalb -> schliessen (Klicks im Panel lassen es offen)
        panel.classList.remove("open");
    }
});
