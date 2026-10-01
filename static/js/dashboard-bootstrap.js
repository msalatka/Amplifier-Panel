function updateOverviewCharts() {
	return loadXmlHistory()
}
function updateStatisticsTable() {
	return loadXmlStatistics()
}
const runningRefreshJobs = new Set()
async function runRefreshJob(name, callback) {
	if (runningRefreshJobs.has(name)) return
	runningRefreshJobs.add(name)
	try {
		await callback()
	} catch (error) {
		console.error(`Refresh job ${name} failed:`, error)
	} finally {
		runningRefreshJobs.delete(name)
	}
}
async function startDataRefresh() {
	await refreshDeviceList()
	if (selectedDeviceId) {
		await updateDashboard()
		await updateStatisticsTable()
	}

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
		const devices = result.devices || []
		if (!devices.some((device) => device.id === selectedDeviceId)) {
			if (devices.length)
				window.location.assign(`/?device=${encodeURIComponent(devices[0].id)}`)
			return
		}
		const list = document.querySelector('.device-switcher-list')
		if (!list) return
		const signature = JSON.stringify(
			devices.map(({ id, label, display_group }) => [id, label, display_group]),
		)
		if (list.dataset.inventorySignature === signature) {
			devices.forEach((device) => {
				const status = list.querySelector(`[data-device-status="${CSS.escape(device.id)}"]`)
				if (status)
					status.textContent = device.connected ? 'Data current' : 'No current data'
			})
			return
		}
		const previousGroup = { value: null }
		const links = devices.map((device) => {
			const link = document.createElement('a')
			link.href = `/?device=${encodeURIComponent(device.id)}`
			link.dataset.deviceLink = device.id
			if (device.id === selectedDeviceId) link.setAttribute('aria-current', 'page')
			if (previousGroup.value !== null && previousGroup.value !== device.display_group) {
				link.classList.add('device-group-separator')
			}
			previousGroup.value = device.display_group
			const label = document.createElement('span')
			label.textContent = device.label
			const status = document.createElement('small')
			status.dataset.deviceStatus = device.id
			status.textContent = device.connected ? 'Data current' : 'No current data'
			link.append(label, status)
			return link
		})
		list.replaceChildren(...links)
		list.dataset.inventorySignature = signature
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

setInterval(() => {
	if (selectedDeviceId) runRefreshJob('dashboard', updateDashboard)
}, 1000)
setInterval(() => runRefreshJob('devices', refreshDeviceList), 3000)
setInterval(() => {
	if (!currentUser) return

	const overviewTab = document.querySelector('.tab-panel[data-tab="overview"]')
	if (
		selectedDeviceId &&
		overviewTab &&
		overviewTab.classList.contains('active') &&
		!xmlHistoryBusy &&
		Date.now() - lastOverviewChartRefresh >=
			historyRefreshInterval(document.getElementById('xml-history-range').value)
	) {
		runRefreshJob('overview', updateOverviewCharts)
	}
	const snmpTab = document.querySelector('.tab-panel[data-tab="snmp-settings"]')
	if (snmpTab && snmpTab.classList.contains('active')) runRefreshJob('snmp', updateSnmpLiveValues)
	const controlTab = document.querySelector('.tab-panel[data-tab="device-control"]')
	if (selectedDeviceId && controlTab && controlTab.classList.contains('active'))
		runRefreshJob('control', loadDeviceControlStatus)
	const warningsTab = document.querySelector('.tab-panel[data-tab="warnings"]')
	if (selectedDeviceId && warningsTab && warningsTab.classList.contains('active'))
		runRefreshJob('warnings', loadActiveAlarms)

	const ntpTab = document.querySelector('.tab-panel[data-tab="ntp-settings"]')
	if (ntpTab && ntpTab.classList.contains('active')) runRefreshJob('ntp', loadNtpStatus)
	const servicesTab = document.querySelector('.tab-panel[data-tab="service-diagnostics"]')
	if (servicesTab && servicesTab.classList.contains('active'))
		runRefreshJob('services', loadServiceDiagnostics)

	const statisticsTab = document.querySelector('.tab-panel[data-tab="statistics"]')
	if (
		selectedDeviceId &&
		statisticsTab &&
		statisticsTab.classList.contains('active') &&
		!xmlStatisticsBusy &&
		Date.now() - lastStatisticsRefresh >=
			historyRefreshInterval(document.getElementById('xml-statistics-range').value)
	) {
		runRefreshJob('statistics', updateStatisticsTable)
	}
}, 3000)
