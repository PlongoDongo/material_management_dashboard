/* ==========================================================================
   Öffnen/Schliessen der beiden Sidebars -- clientseitig.

   Reine className-Umschaltung (Sidebar + Overlay bekommen/verlieren "open").
   Frueher lief das als Server-Callback: jeder Klick kostete eine HTTP-Runde,
   bevor die Animation ueberhaupt startete -- bei hoher Latenz deutlich
   spuerbar. Im Browser schaltet es ohne Server-Runde sofort um; sichtbar
   bleibt nur noch die CSS-Transition.

   Logik wie zuvor in Python:
     - Menue-/Filter-Icon  -> umschalten
     - Overlay / Schliessen -> immer schliessen
   ========================================================================== */
window.dash_clientside = window.dash_clientside || {};

function _sidebarClasses(base, isOpen) {
    // Rueckgabe: [sidebar-className, overlay-className]
    return isOpen
        ? [base + " open", "sidebar-overlay open"]
        : [base, "sidebar-overlay"];
}

function _toggle(openBtnId, base, currentCls) {
    var ctx = window.dash_clientside.callback_context;
    var trig = ctx && ctx.triggered && ctx.triggered.length
        ? ctx.triggered[0].prop_id.split(".")[0] : "";
    var isOpen = (currentCls || "").indexOf("open") !== -1;
    // Nur der Oeffnen-Button schaltet um; Overlay/Schliessen schliessen immer.
    isOpen = (trig === openBtnId) ? !isOpen : false;
    return _sidebarClasses(base, isOpen);
}

window.dash_clientside.sidebar = {
    toggleNav: function (_menu, _overlay, _close, cls) {
        return _toggle("menu-btn", "sidebar sidebar-nav", cls);
    },
    toggleFilter: function (_icon, _overlay, _close, cls) {
        return _toggle("filter-btn", "sidebar sidebar-filter", cls);
    },
};
