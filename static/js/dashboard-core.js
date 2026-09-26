// Shared state and helpers for the four XML devices.
const navLinks = document.querySelectorAll('.nav-link')
const tabPanels = document.querySelectorAll('.tab-panel')
const currentTitle = document.getElementById('current-tab-title')
const selectedDeviceId = document.body.dataset.deviceId
const deviceProfile = document.body.dataset.deviceProfile
let currentUser = null
let latestNetwork = null
let serviceSettingsDirty = false
let latestServiceDatabase = {}
let lastOverviewChartRefresh = 0
let lastStatisticsRefresh = 0
const chartSeriesVisibility = new Map()
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
	const existing = Array.from(container.children).find(
		(item) => item.dataset.message === message && item.dataset.type === type,
	)
	if (existing) {
		const count = Number(existing.dataset.repeatCount || 1) + 1
		existing.dataset.repeatCount = String(count)
		existing.querySelector('.notification-count').textContent = `×${count}`
		window.clearTimeout(Number(existing.dataset.dismissTimer))
		existing.classList.remove('repeated')
		void existing.offsetWidth
		existing.classList.add('repeated')
		existing.dataset.dismissTimer = String(window.setTimeout(() => existing.remove(), 5000))
		return
	}
	const notification = document.createElement('div')
	notification.className = `notification ${type}`
	notification.setAttribute('role', type === 'error' ? 'alert' : 'status')
	notification.dataset.message = message
	notification.dataset.type = type
	notification.dataset.repeatCount = '1'
	const text = document.createElement('span')
	text.textContent = message
	const count = document.createElement('span')
	count.className = 'notification-count'
	count.setAttribute('aria-label', 'Notification repeat count')
	count.textContent = '×1'
	notification.append(text, count)
	container.appendChild(notification)
	notification.dataset.dismissTimer = String(window.setTimeout(() => notification.remove(), 5000))
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
	if (tabName === 'device-control') loadDeviceControlStatus()
	if (tabName === 'warnings') loadActiveAlarms()
	if (tabName === 'access-control') loadAccessUsers()
	if (tabName === 'snmp-settings') loadSnmpSettings()
	if (tabName === 'network-settings') loadNetworkSettings()
	if (tabName === 'ntp-settings') loadNtpStatus()
	if (tabName === 'service-diagnostics') loadServiceDiagnostics()
	if (tabName === 'variable-blocks') loadXmlMapping()
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
		element.disabled = !canOperate()
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
		const activeTab = document.querySelector('.nav-link.active')?.dataset.tab
		if (targetTab === activeTab) return
		if (
			typeof window.confirmDiscardXmlMappingChanges === 'function' &&
			!window.confirmDiscardXmlMappingChanges()
		) {
			return
		}
		if (!setActiveTab(targetTab)) return
		history.pushState(
			null,
			'',
			`${window.location.pathname}${window.location.search}#${encodeURIComponent(targetTab)}`,
		)
	})
})

window.addEventListener('popstate', () => {
	if (!currentUser) return
	const activeTab = document.querySelector('.nav-link.active')?.dataset.tab || 'standard-view'
	const targetTab = decodeURIComponent(window.location.hash.slice(1)) || 'standard-view'
	if (
		targetTab !== activeTab &&
		typeof window.confirmDiscardXmlMappingChanges === 'function' &&
		!window.confirmDiscardXmlMappingChanges()
	) {
		history.pushState(
			null,
			'',
			`${window.location.pathname}${window.location.search}#${encodeURIComponent(activeTab)}`,
		)
		return
	}
	restoreTabFromUrl()
})
