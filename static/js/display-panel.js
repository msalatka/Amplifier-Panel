// display-panel.js
// Zasila kartę "Display" (6 sekcji: Laser/Uplink/P1-P7/Synth/TEC/System)
// danymi z istniejącego API. Działa niezależnie od dashboard.js - nie modyfikuje
// ani nie zależy od jego wewnętrznej logiki, tylko odpytuje te same endpointy.

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

            setText("dp-laser-state", stateLabel);
            setText("dp-laser-gain-set", fmt(data.gain_set, "dB"));
            setText("dp-laser-gain-actual", fmt(data.gain_actual, "dB"));
            setText("dp-laser-gain-delta", fmt(data.gain_delta, "dB"));

            setText("dp-uplink-state", stateLabel);
            setText("dp-uplink-power", fmt(data.PiA, "dBm"));

            setText("dp-port1-state", stateLabel);
            setText("dp-port1-power", fmt(data.PoA, "dBm"));
            setText("dp-port2-state", stateLabel);
            setText("dp-port2-power", fmt(data.PoB, "dBm"));

            setText("dp-tec-temperature", fmt(data.temperature, "\u00b0C"));
        } catch (error) {
            console.error("display-panel: failed to refresh /api/latest", error);
        }
    }

    async function refreshNetwork() {
        try {
            const response = await fetch("/api/network", { credentials: "same-origin" });
            if (!response.ok) throw new Error("not authorized or unavailable");
            const state = await response.json();
            setText("dp-system-hostname", state.hostname || "--");
            const selected = (state.interfaces || []).find(i => i.name === state.selected_interface);
            setText("dp-system-ip", selected ? (selected.ip_address || "--") : "--");
            setText("dp-system-netmask", selected ? (selected.netmask || "--") : "--");
        } catch (error) {
            setText("dp-system-hostname", "No data");
            setText("dp-system-ip", "No data");
            setText("dp-system-netmask", "No data");
        }
    }

    function tickClock() {
        setText("dp-system-time", new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC");
    }

    refreshLatest();
    refreshNetwork();
    tickClock();
    setInterval(refreshLatest, REFRESH_MS);
    setInterval(tickClock, 1000);
})();
