(function () {
    const REFRESH_MS = 3000;
    let selectedPort = 1;
 
    function fmt(value, unit, decimals) {
        if (value === undefined || value === null || Number.isNaN(value)) return "--";
        return Number(value).toFixed(decimals !== undefined ? decimals : 2) + " " + unit;
    }
 
    function renderDetailCard(data, connected) {
        const card = document.getElementById("lvp-detail-card");
        if (!card) return;
 
        if (selectedPort > 2) {
            card.innerHTML =
                '<div class="lvp-detail-row"><span>Port ' + selectedPort + '</span><strong class="lvp-unequipped">Unequipped</strong></div>' +
                '<div class="lvp-detail-row"><span>Description</span><strong class="lvp-unequipped">Slot available for future expansion</strong></div>';
            return;
        }
 
        const label = selectedPort === 1 ? "A (PoA)" : "B (PoB)";
        const powerValue = selectedPort === 1 ? data.PoA : data.PoB;
        const stateLabel = connected ? "CONNECTED" : "DISCONNECTED";
 
        card.innerHTML =
            '<div class="lvp-detail-row"><span>Port ' + selectedPort + ' - Type</span><strong>Output ' + (selectedPort === 1 ? "A" : "B") + '</strong></div>' +
            '<div class="lvp-detail-row"><span>State</span><strong>' + stateLabel + '</strong></div>' +
            '<div class="lvp-detail-row"><span>Estimated Optical Power</span><strong>' + fmt(powerValue, "dBm") + '</strong></div>' +
            '<div class="lvp-detail-row"><span>Description</span><strong>Output port ' + label + '</strong></div>';
    }
 
    function selectPort(portNumber) {
        selectedPort = portNumber;
        document.querySelectorAll(".lvp-port-btn").forEach(btn => {
            btn.classList.toggle("active", Number(btn.dataset.port) === portNumber);
        });
        refreshLatest();
    }
 
    async function refreshLatest() {
        try {
            const response = await fetch("/api/latest", { credentials: "same-origin" });
            if (!response.ok) return;
            const payload = await response.json();
            renderDetailCard(payload.data || {}, !!payload.connected);
        } catch (error) {
            console.error("live-view-ports: failed to refresh /api/latest", error);
        }
    }
 
    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".lvp-port-btn").forEach(btn => {
            btn.addEventListener("click", () => selectPort(Number(btn.dataset.port)));
        });
        refreshLatest();
        setInterval(refreshLatest, REFRESH_MS);
    });
})();
