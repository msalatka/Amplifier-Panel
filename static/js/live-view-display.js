(function () {
    const REFRESH_MS = 3000;
 
    function fmt(value, unit, decimals) {
        if (value === undefined || value === null || Number.isNaN(value)) return "--";
        return Number(value).toFixed(decimals !== undefined ? decimals : 2) + " " + unit;
    }
 
    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }
 
    async function refreshLatest() {
        try {
            const response = await fetch("/api/latest", { credentials: "same-origin" });
            if (!response.ok) return;
            const payload = await response.json();
            const data = payload.data || {};
            const connected = !!payload.connected;
            const stateLabel = connected ? "CONNECTED" : "DISCONNECTED";
 
            setText("lv-laser-state", stateLabel);
            setText("lv-laser-gain-set", fmt(data.gain_set, "dB"));
            setText("lv-laser-gain-actual", fmt(data.gain_actual, "dB"));
            setText("lv-laser-gain-delta", fmt(data.gain_delta, "dB"));
 
            setText("lv-uplink-state", stateLabel);
            setText("lv-uplink-power", fmt(data.PiA, "dBm"));
 
            setText("lv-port1-state", stateLabel);
            setText("lv-port1-power", fmt(data.PoA, "dBm"));
            setText("lv-port2-state", stateLabel);
            setText("lv-port2-power", fmt(data.PoB, "dBm"));
 
            setText("lv-tec-temperature", fmt(data.temperature, "\u00b0C"));
        } catch (error) {
            console.error("live-view-display: failed to refresh /api/latest", error);
        }
    }
 
    async function refreshNetwork() {
        try {
            const response = await fetch("/api/network", { credentials: "same-origin" });
            if (!response.ok) throw new Error("not authorized or unavailable");
            const state = await response.json();
            setText("lv-system-hostname", state.hostname || "--");
            const selected = (state.interfaces || []).find(i => i.name === state.selected_interface);
            setText("lv-system-ip", selected ? (selected.ip_address || "--") : "--");
            setText("lv-system-netmask", selected ? (selected.netmask || "--") : "--");
        } catch (error) {
            setText("lv-system-hostname", "No data");
            setText("lv-system-ip", "No data");
            setText("lv-system-netmask", "No data");
        }
    }
 
    function tickClock() {
        setText("lv-system-time", new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC");
    }
 
    function setupTabs() {
        document.querySelectorAll(".lv-tab-btn").forEach(button => {
            button.addEventListener("click", () => {
                document.querySelectorAll(".lv-tab-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".lv-tab-panel").forEach(p => p.classList.remove("active"));
                button.classList.add("active");
                const target = document.querySelector('.lv-tab-panel[data-lvpanel="' + button.dataset.lvtab + '"]');
                if (target) target.classList.add("active");
            });
        });
    }
 
    document.addEventListener("DOMContentLoaded", () => {
        setupTabs();
        refreshLatest();
        refreshNetwork();
        tickClock();
        setInterval(refreshLatest, REFRESH_MS);
        setInterval(tickClock, 1000);
    });
})();
