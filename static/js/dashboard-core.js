// Shared application state, formatting and navigation helpers.
/**
 * @typedef {Object} FtsModuleStatus
 * @property {string} name Physical slot name such as UL or P1.
 * @property {string} type Installed module type or Unequipped.
 * @property {string} state Normalized device state.
 * @property {string[]} [connectors] Physical connector codes reported for the module.
 */

/**
 * @typedef {Object} FtsStatus
 * @property {Object<string, *>} laser
 * @property {FtsModuleStatus} uplink
 * @property {FtsModuleStatus[]} ports Seven physical P1-P7 slots.
 * @property {Object<string, *>} synth
 * @property {Object<string, *>} tec
 * @property {Object<string, *>} system
 */

const navLinks = document.querySelectorAll('.nav-link')
const tabPanels = document.querySelectorAll('.tab-panel')
const currentTitle = document.getElementById('current-tab-title')

let dashboardSettings = null
let overviewRange = '5m'
let statisticsRange = '5m'
let powerChart = null
let gainChart = null
let deltaChart = null
let temperatureChart = null
let ftsOpticalPowerChart = null
let ftsLfNoiseChart = null
let ftsHfNoiseChart = null
let ftsJitterChart = null
let ftsLaserFrequencyChart = null
let ftsTecChart = null
let gainInputEdited = false
let currentUser = null
let overviewStart = null
let overviewEnd = null
let statisticsStart = null
let statisticsEnd = null
let latestNetwork = null
let lastOverviewChartRefresh = 0
let lastStatisticsRefresh = 0
let statisticsRequestController = null
let statisticsRequestSequence = 0
let overviewRequestController = null
let overviewRequestSequence = 0
let serviceSettingsDirty = false
let latestServiceDatabase = {}
let warningHistoryOffset = 0
let warningHistoryTotal = 0
let warningHistoryStart = null
let warningHistoryEnd = null
let lastWarningHistoryRefresh = 0
const warningHistoryLimit = 100
const chartSeriesVisibility = new Map()
const deviceProfile = document.body.dataset.deviceProfile || 'amplifier'
let latestFtsStatus = null

function historyRefreshInterval(rangeValue) {
	return (
		{
			'5m': 3000,
			'1h': 3000,
			'24h': 10000,
			'7d': 15000,
			'30d': 30000,
			all: 60000,
		}[rangeValue] || 3000
	)
}

function formatDbm(value) {
	if (value === null || value === undefined) return '-- dBm'
	return Number(value).toFixed(2) + ' dBm'
}

function formatDb(value) {
	if (value === null || value === undefined) return '-- dB'
	return Number(value).toFixed(2) + ' dB'
}

function formatTemperature(value) {
	if (value === null || value === undefined) return '-- \u00B0C'
	return Number(value).toFixed(2) + ' \u00B0C'
}

function formatTime(value) {
	if (!value) return '--'
	return new Date(value).toLocaleTimeString()
}

function formatPlainNumber(value, digits = 2) {
	if (value === null || value === undefined || value === '') return '--'
	const numeric = Number(value)
	return Number.isFinite(numeric) ? numeric.toFixed(digits) : String(value)
}

function localDateTimeToIso(value) {
	if (!value) return null
	const date = new Date(value)
	if (Number.isNaN(date.getTime())) return null
	return date.toISOString()
}

function buildRangeQuery(rangeValue, startValue, endValue) {
	const params = new URLSearchParams({ range: rangeValue })

	if (startValue) {
		params.set('start', startValue)
	}

	if (endValue) {
		params.set('end', endValue)
	}

	return params.toString()
}

function buildOverviewQuery() {
	return buildRangeQuery(overviewRange, overviewStart, overviewEnd)
}

function buildStatisticsQuery() {
	return buildRangeQuery(statisticsRange, statisticsStart, statisticsEnd)
}

function escapeHtml(value) {
	return String(value)
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;')
		.replaceAll("'", '&#039;')
}

function showNotification(message, type = 'success') {
	const container = document.getElementById('notification-container')
	if (!container) return
	const notification = document.createElement('div')
	notification.className = `notification ${type}`
	notification.setAttribute('role', type === 'error' ? 'alert' : 'status')
	notification.textContent = message
	container.appendChild(notification)
	window.setTimeout(() => notification.remove(), 5000)
}

function apiErrorMessage(detail, fallback) {
	if (typeof detail === 'string' && detail.trim()) return detail
	if (Array.isArray(detail)) {
		const messages = detail
			.map((item) => {
				if (!item || typeof item !== 'object') return String(item || '')
				const location = Array.isArray(item.loc)
					? item.loc.filter((part) => part !== 'body').join('.')
					: ''
				const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(item)
				return location ? `${location}: ${message}` : message
			})
			.filter(Boolean)
		if (messages.length) return messages.join('; ')
	}
	if (detail && typeof detail === 'object') {
		if (typeof detail.message === 'string') return detail.message
		try {
			return JSON.stringify(detail)
		} catch {
			// Fall through to the caller-provided message.
		}
	}
	return fallback
}

function firstValue(object, keys, fallback = null) {
	for (const key of keys) {
		if (object && object[key] !== undefined && object[key] !== null && object[key] !== '')
			return object[key]
	}
	return fallback
}

function displayValue(value, unit = '') {
	if (value === null || value === undefined || value === '') return '--'
	if (typeof value === 'boolean') return value ? 'ON' : 'OFF'
	return `${value}${unit}`
}

function displayMeasurement(value, unit) {
	if (value === null || value === undefined || value === '') return '--'
	if (typeof value === 'boolean') return value ? 'ON' : 'OFF'
	const raw = String(value).trim()
	if (!raw || raw === '-' || raw === '--') return '--'
	const numeric = raw.match(/^[-+]?\d+(?:[.,]\d+)?/)
	if (!numeric) return raw
	return `${numeric[0].replace(',', '.')} ${unit}`
}

function ftsStateClass(value) {
	const normalized = String(value ?? 'unknown')
		.trim()
		.toLowerCase()
	return !normalized || ['unknown', '-', '--'].includes(normalized) ? 'unknown' : 'reported'
}

function ftsMetric(label, value, unit = '') {
	return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(displayValue(value, unit))}</dd></div>`
}

function ftsModuleTarget(module, index, uplink = false) {
	if (uplink) return 'ul'
	const nameMatch = String(module?.name || '').match(/(?:port|p)\s*([0-9]+)/i)
	return `port${nameMatch ? nameMatch[1] : index + 1}`
}

function ftsModuleIsEquipped(module) {
	const type = String(firstValue(module, ['type'], 'Unknown')).toLowerCase()
	const stateValue = String(firstValue(module, ['state'], 'UNKNOWN')).toLowerCase()
	return !type.includes('unequipped') && stateValue !== 'unequipped'
}

function ftsConnectorLabel(connector) {
	const labels = {
		O: 'Optical',
		BN: 'Beat note',
		BNA: 'Amplified beat note',
		BN_A: 'Amplified beat note',
		TR: 'Tracking oscillator output',
	}
	return labels[connector] || connector
}

function ftsConnectorCode(connector) {
	return connector === 'BN_A' ? 'BNA' : connector
}

function renderFtsModule(module, index, uplink = false) {
	const stateValue = firstValue(module, ['state'], 'UNKNOWN')
	const stateClass = ftsStateClass(stateValue)
	const type = firstValue(module, ['type'], 'Unknown')
	const unequipped = !ftsModuleIsEquipped(module)
	const target = ftsModuleTarget(module, index, uplink)
	const slotLabel = uplink ? 'UPLINK' : `SLOT ${index + 1}`
	const metrics = []
	if (!unequipped) {
		metrics.push(
			ftsMetric(
				'Optical input',
				displayMeasurement(
					firstValue(module, ['optical_power_display', 'optical_power']),
					'dBm',
				),
			),
		)
		metrics.push(
			ftsMetric(
				'LF / HF noise',
				`${displayValue(firstValue(module, ['noise_lf']))} / ${displayValue(firstValue(module, ['noise_hf']))}`,
			),
		)
		if (firstValue(module, ['jitter']) !== null)
			metrics.push(
				ftsMetric('Jitter', displayMeasurement(firstValue(module, ['jitter']), '%')),
			)
		if (firstValue(module, ['distance_km']) !== null)
			metrics.push(
				ftsMetric(
					'Equivalent distance',
					displayMeasurement(firstValue(module, ['distance_km']), 'km'),
				),
			)
	}
	const connectors = (module.connectors || [])
		.map(
			(connector) => `
		<span class="fts-connector" title="${escapeHtml(ftsConnectorLabel(connector))}">
			<span class="fts-connector-socket" aria-hidden="true"></span>
			<span>${escapeHtml(ftsConnectorCode(connector))}</span>
		</span>`,
		)
		.join('')
	return `
		<article class="fts-module fts-pluggable-module ${stateClass} ${unequipped ? 'unequipped' : ''}" data-fts-module-target="${escapeHtml(target)}"${unequipped ? '' : ' tabindex="0"'}>
			<div class="fts-slot-label">${escapeHtml(slotLabel)}</div>
			<div class="fts-module-title"><span class="fts-led ${stateClass}"></span><strong>${escapeHtml(module.name || slotLabel)}</strong><small>${escapeHtml(type)}</small></div>
			<dl class="fts-metrics">
				${ftsMetric('State', stateValue)}
				${metrics.join('')}
				${ftsMetric('Description', firstValue(module, ['description']))}
			</dl>
			<div class="fts-port-connectors">${connectors || '<span class="fts-no-connectors">No physical ports</span>'}</div>
		</article>`
}

function syncFtsModuleTargets(modules) {
	const select = document.getElementById('fts-target')
	if (!select) return
	const selected = select.value
	select.replaceChildren(
		...modules.map(({ module, index, uplink }) => {
			const option = document.createElement('option')
			option.value = ftsModuleTarget(module, index, uplink)
			option.textContent = `${module.name || (uplink ? 'UL' : `P${index + 1}`)} · ${firstValue(module, ['type'], 'Unknown')}`
			option.disabled = !ftsModuleIsEquipped(module)
			return option
		}),
	)
	if ([...select.options].some((option) => option.value === selected && !option.disabled))
		select.value = selected
	else select.value = [...select.options].find((option) => !option.disabled)?.value || ''
}

function highlightSelectedFtsModule() {
	const selected = document.getElementById('fts-target')?.value
	document.querySelectorAll('[data-fts-module-target]').forEach((module) => {
		module.classList.toggle(
			'selected',
			Boolean(selected) && module.dataset.ftsModuleTarget === selected,
		)
	})
}

/** Render a normalized station snapshot without assuming which slots are equipped.
 * @param {FtsStatus} status
 */
function renderFtsStatus(status) {
	if (!status) return
	latestFtsStatus = status
	const laser = status.laser || {}
	const synth = status.synth || {}
	const tec = status.tec || {}
	const laserState = firstValue(laser, ['state'], '--')
	setTextIfExists('fts-laser-state', displayValue(laserState))
	setTextIfExists(
		'fts-laser-frequency',
		displayMeasurement(firstValue(laser, ['optical_frequency']), 'GHz'),
	)
	setTextIfExists(
		'fts-laser-wavelength',
		displayMeasurement(firstValue(laser, ['optical_wavelength']), 'nm'),
	)
	setTextIfExists(
		'fts-laser-centre',
		displayMeasurement(firstValue(laser, ['central_frequency_set']), 'GHz'),
	)
	setTextIfExists(
		'fts-laser-span',
		displayMeasurement(firstValue(laser, ['scanning_frequency_span_set']), 'MHz'),
	)
	const laserLed = document.getElementById('fts-laser-led')
	if (laserLed) laserLed.className = `fts-led ${ftsStateClass(laserState)}`

	const synthState = firstValue(synth, ['state'], '--')
	setTextIfExists('fts-synth-state', displayValue(synthState))
	setTextIfExists(
		'fts-synth-reference',
		displayValue(firstValue(synth, ['10_mhz_reference_source'])),
	)
	setTextIfExists('fts-synth-external', displayValue(firstValue(synth, ['external_10_mhz'])))
	const synthLed = document.getElementById('fts-synth-led')
	if (synthLed) synthLed.className = `fts-led ${ftsStateClass(synthState)}`

	const tecState = firstValue(tec, ['state'], '--')
	setTextIfExists('fts-tec-state', displayValue(tecState))
	setTextIfExists(
		'fts-tec-temperature',
		`${displayMeasurement(firstValue(tec, ['temperature_set_c']), '°C')} / ${displayMeasurement(firstValue(tec, ['temperature_read_c']), '°C')}`,
	)
	setTextIfExists(
		'fts-tec-power',
		displayMeasurement(firstValue(tec, ['power_usage_percent']), '%'),
	)
	const tecLed = document.getElementById('fts-tec-led')
	if (tecLed) tecLed.className = `fts-led ${ftsStateClass(tecState)}`

	const inventory = [
		...(status.uplink ? [{ module: status.uplink, index: 0, uplink: true }] : []),
		...(status.ports || []).map((module, index) => ({ module, index, uplink: false })),
	]
	const portPositions = inventory.filter((item) => !item.uplink)
	const equippedPorts = portPositions.filter((item) => ftsModuleIsEquipped(item.module))
	const uplinkEquipped = inventory.some((item) => item.uplink && ftsModuleIsEquipped(item.module))
	const slotCount = portPositions.length
	const modules = document.getElementById('fts-modules')
	if (modules) {
		modules.innerHTML = inventory.length
			? inventory
					.map((item) => renderFtsModule(item.module, item.index, item.uplink))
					.join('')
			: '<p class="fts-empty-rack">No optical modules were reported by the station.</p>'
	}
	setTextIfExists(
		'fts-equipped-count',
		`${equippedPorts.length} / ${slotCount} positions equipped`,
	)
	setTextIfExists(
		'fts-rack-summary',
		`${equippedPorts.length} of ${slotCount} configurable port positions equipped; uplink ${uplinkEquipped ? 'present' : 'unavailable'}.`,
	)
	setTextIfExists(
		'fts-module-inventory',
		inventory.length
			? `UL + ${equippedPorts.length} of ${slotCount} modular ports equipped`
			: 'No module inventory received',
	)
	syncFtsModuleTargets(inventory)
	highlightSelectedFtsModule()
	updateFtsSettingsForms()
}

function formatDateTime(value) {
	if (!value) return '--'
	const date = new Date(value)
	return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

function formatDuration(seconds) {
	if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '--'
	const total = Math.max(0, Math.round(Number(seconds)))
	const hours = Math.floor(total / 3600)
	const minutes = Math.floor((total % 3600) / 60)
	const remainingSeconds = total % 60
	if (hours) return `${hours} h ${minutes} min`
	if (minutes) return `${minutes} min ${remainingSeconds} s`
	return `${remainingSeconds} s`
}

async function responseError(response, fallback) {
	try {
		const body = await response.json()
		return new Error(apiErrorMessage(body.detail, fallback))
	} catch {
		return new Error(fallback)
	}
}

function valueOrNull(input) {
	if (!input || input.value === '') return null
	return Number(input.value)
}

function setInputValue(selector, value) {
	const input = document.querySelector(selector)
	if (!input) return
	input.value = value === null || value === undefined ? '' : value
}

function setTextIfExists(id, value) {
	const element = document.getElementById(id)
	if (element) element.textContent = value
}

function handleAuthResponse(response) {
	if (response.status === 401) {
		currentUser = null
		showLogin()
		throw new Error('Not authenticated')
	}

	if (response.status === 403) {
		throw new Error('Not allowed')
	}
}

function isAdministrator() {
	return currentUser && currentUser.role === 'Administrator'
}

function canOperate() {
	return currentUser && (currentUser.role === 'Administrator' || currentUser.role === 'Operator')
}

function setActiveTab(tabName) {
	const targetLink = document.querySelector(`.nav-link[data-tab="${tabName}"]`)
	if (
		!targetLink ||
		(targetLink.hasAttribute('data-admin-only') && !isAdministrator()) ||
		(targetLink.hasAttribute('data-operator-only') && !canOperate())
	) {
		return false
	}

	navLinks.forEach((item) => item.classList.remove('active'))
	targetLink.classList.add('active')

	tabPanels.forEach((panel) => {
		panel.classList.toggle('active', panel.dataset.tab === tabName)
	})

	currentTitle.textContent = targetLink.dataset.title

	if (tabName === 'overview') updateOverviewCharts()
	if (tabName === 'statistics') updateStatisticsTable()
	if (tabName === 'warnings') updateWarningsTable(true)
	if (tabName === 'access-control') loadAccessUsers()
	if (tabName === 'snmp-settings') loadSnmpSettings()
	if (tabName === 'network-settings') loadNetworkSettings()
	if (tabName === 'ntp-settings') loadNtpStatus()
	if (tabName === 'service-diagnostics') loadServiceDiagnostics()
	return true
}

function restoreTabFromUrl() {
	const requestedTab = decodeURIComponent(window.location.hash.slice(1))
	const tabName = requestedTab || 'standard-view'

	if (!setActiveTab(tabName)) {
		setActiveTab('standard-view')
		history.replaceState(
			null,
			'',
			`${window.location.pathname}${window.location.search}#standard-view`,
		)
	}
}

function applyRoleUi() {
	document.querySelectorAll('[data-admin-only]').forEach((element) => {
		element.hidden = !isAdministrator()
	})

	document.querySelectorAll('[data-operator-control]').forEach((element) => {
		element.disabled = !canOperate() || (deviceProfile === 'fts-ls' && !!element.closest('.fts-settings-panel'))
	})

	document.querySelectorAll('[data-operator-only]').forEach((element) => {
		element.hidden = !canOperate()
	})

	if (currentUser) {
		setTextIfExists('current-user-label', `${currentUser.username} (${currentUser.role})`)
	}

	const activeTab = document.querySelector('.tab-panel.active')
	if (
		activeTab &&
		((activeTab.hasAttribute('data-admin-only') && !isAdministrator()) ||
			(activeTab.hasAttribute('data-operator-only') && !canOperate()))
	) {
		setActiveTab('standard-view')
	}
}

function showLogin() {
	document.getElementById('login-screen').classList.remove('app-hidden')
	document.getElementById('app-layout').classList.add('app-hidden')
}

function showApp() {
	document.getElementById('login-screen').classList.add('app-hidden')
	document.getElementById('app-layout').classList.remove('app-hidden')
}

navLinks.forEach((link) => {
	link.addEventListener('click', () => {
		const targetTab = link.dataset.tab
		if (!setActiveTab(targetTab)) return
		history.pushState(
			null,
			'',
			`${window.location.pathname}${window.location.search}#${encodeURIComponent(targetTab)}`,
		)
	})
})

window.addEventListener('popstate', () => {
	if (currentUser) restoreTabFromUrl()
})
