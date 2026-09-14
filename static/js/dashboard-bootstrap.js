// Profile dispatch, startup and periodic refresh scheduling.
function updateOverviewCharts() {
	return deviceProfile === 'fts-ls' ? loadFtsOverview() : updateAmplifierOverviewCharts()
}

function updateStatisticsTable() {
	return deviceProfile === 'fts-ls' ? loadFtsStatistics() : updateAmplifierStatisticsTable()
}

async function startDataRefresh() {
	if (selectedDeviceId === 'amplifier') await loadSettings()
	await refreshDeviceList()
	await updateDashboard()
	if (selectedDeviceId === 'amplifier') await updateWarningsTable()
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
			if (status) status.textContent = device.connected ? 'Connected' : 'Disconnected'
		}
	} catch (error) {
		console.error('Could not refresh device list:', error)
	}
}

setupSettingsButtons()
setupWarningFilters()
setupRangeButtons()
setupAccessControl()
setupAuth()
setupChartExpansion()
setupFtsControls()

checkAuth().then((isAuthenticated) => {
	if (isAuthenticated) {
		startDataRefresh()
	}
})

setInterval(updateDashboard, 1000)
setInterval(refreshDeviceList, 3000)
setInterval(() => { if (selectedDeviceId === 'amplifier') updateWarningsTable() }, 3000)
setInterval(() => {
	if (!currentUser) return

	const overviewTab = document.querySelector('.tab-panel[data-tab="overview"]')
	if (
		overviewTab &&
		overviewTab.classList.contains('active') &&
		!overviewRequestController &&
		Date.now() - lastOverviewChartRefresh >= historyRefreshInterval(overviewRange)
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
		!statisticsRequestController &&
		Date.now() - lastStatisticsRefresh >= historyRefreshInterval(statisticsRange)
	) {
		updateStatisticsTable()
	}
}, 3000)
