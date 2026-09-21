function updateOverviewCharts() {
	return loadXmlHistory()
}
function updateStatisticsTable() {
	return loadXmlStatistics()
}
async function startDataRefresh() {
	await refreshDeviceList()
	await updateDashboard()
	await updateStatisticsTable()

	if (isAdministrator()) {
		await loadAccessUsers()
	}
}

async function refreshDeviceList() {
	if (!currentUser) return
	try {
		const response = await fetch('/api/devices')
		handleAuthResponse(response)
		if (!response.ok) return
		const result = await response.json()
		for (const device of result.devices || []) {
			const status = [...document.querySelectorAll('[data-device-status]')].find(
				(element) => element.dataset.deviceStatus === device.id,
			)
			if (status) status.textContent = device.connected ? 'Data current' : 'No current data'
		}
	} catch (error) {
		console.error('Could not refresh device list:', error)
	}
}

setupAccessControl()
setupAuth()

checkAuth().then((isAuthenticated) => {
	if (isAuthenticated) {
		startDataRefresh()
	}
})

setInterval(updateDashboard, 1000)
setInterval(refreshDeviceList, 3000)
setInterval(() => {
	if (!currentUser) return

	const overviewTab = document.querySelector('.tab-panel[data-tab="overview"]')
	if (
		overviewTab &&
		overviewTab.classList.contains('active') &&
		!xmlHistoryBusy &&
		Date.now() - lastOverviewChartRefresh >=
			historyRefreshInterval(document.getElementById('xml-history-range').value)
	) {
		updateOverviewCharts()
	}
	const snmpTab = document.querySelector('.tab-panel[data-tab="snmp-settings"]')
	if (snmpTab && snmpTab.classList.contains('active')) updateSnmpLiveValues()

	const ntpTab = document.querySelector('.tab-panel[data-tab="ntp-settings"]')
	if (ntpTab && ntpTab.classList.contains('active')) loadNtpStatus()
	const servicesTab = document.querySelector('.tab-panel[data-tab="service-diagnostics"]')
	if (servicesTab && servicesTab.classList.contains('active')) loadServiceDiagnostics()

	const statisticsTab = document.querySelector('.tab-panel[data-tab="statistics"]')
	if (
		statisticsTab &&
		statisticsTab.classList.contains('active') &&
		!xmlStatisticsBusy &&
		Date.now() - lastStatisticsRefresh >=
			historyRefreshInterval(document.getElementById('xml-statistics-range').value)
	) {
		updateStatisticsTable()
	}
}, 3000)
