// Device presentation is driven by stable field keys and editable UI roles.
let xmlFields = []
let xmlHistoryBusy = false
let xmlStatisticsBusy = false
let xmlDashboardBusy = false
let xmlLayoutSignature = ''
const xmlCharts = new Map()
let xmlChartGroups = []
let xmlChartLayout = {}
let amplifierLiveFields = []

function fieldValue(snapshot, field) {
	return snapshot.values?.[field.section]?.[field.key] ?? null
}

function fieldIdentifier(field) {
	return `${field.section}:${field.key}`
}

function variableDataAttributes(field) {
	return `data-variable-section="${escapeHtml(field.section)}" data-variable-key="${escapeHtml(field.key)}" data-variable-automatic="${field.automatic ? 'true' : 'false'}"`
}

function setVariableTarget(element, field) {
	if (!element) return
	if (!field) {
		delete element.dataset.variableSection
		delete element.dataset.variableKey
		delete element.dataset.variableAutomatic
		return
	}
	element.dataset.variableSection = field.section
	element.dataset.variableKey = field.key
	element.dataset.variableAutomatic = String(Boolean(field.automatic))
}

function measurementText(snapshot, field) {
	const value = fieldValue(snapshot, field)
	if (value === null) return '--'
	if (field.type === 'boolean') return value ? 'true' : 'false'
	return `${value}${field.unit ? ' ' + field.unit : ''}`
}

function defaultChartLayout() {
	const groups = []
	const layout = {}
	for (const field of xmlFields.filter((item) => item.type === 'number')) {
		const group = `${field.section}:${field.group}`
		if (!groups.includes(group) && groups.length < 8) groups.push(group)
		const chart = groups.indexOf(group) + 1
		if (chart > 0) layout[fieldIdentifier(field)] = chart
	}
	return layout
}

function renderChartSettings() {
	const container = document.getElementById('xml-chart-field-settings')
	container.innerHTML = xmlFields
		.filter((field) => field.type === 'number')
		.map((field) => {
			const identifier = fieldIdentifier(field)
			const selected = xmlChartLayout[identifier] || 0
			const options = ['<option value="0">Hidden</option>']
			for (let chart = 1; chart <= 8; chart += 1) {
				options.push(
					`<option value="${chart}"${selected === chart ? ' selected' : ''}>Chart ${chart}</option>`,
				)
			}
			return `<label><span>${escapeHtml(field.title)}</span><select data-chart-field="${escapeHtml(identifier)}">${options.join('')}</select></label>`
		})
		.join('')
}

function renderXmlStatus(result) {
	const snapshot = result.data || {}
	const sections = snapshot.sections || []
	setTextIfExists('device-live-title', snapshot.label || document.body.dataset.deviceLabel)
	setTextIfExists(
		'device-source-message',
		result.error || (result.connected ? '' : 'Waiting for status.xml'),
	)
	document.getElementById('device-live-board').classList.toggle('xml-stale', !result.connected)
	xmlFields = sections.flatMap((section) =>
		section.fields.map((field) => ({
			...field,
			section: section.key,
			sectionLabel: section.label,
			path: `values.${section.key}.${field.key}`,
			title: `${section.label} / ${field.label}`,
		})),
	)
	renderDeviceControl(snapshot, xmlFields)
	xmlChartLayout = result.chart_layout === null ? defaultChartLayout() : result.chart_layout || {}
	if (deviceProfile === 'amplifier') {
		for (const readout of document.querySelectorAll('[data-readout]')) {
			const field = xmlFields.find((item) => item.role === readout.dataset.readout)
			readout.textContent = field ? measurementText(snapshot, field) : '--'
			setVariableTarget(readout.parentElement, field)
			const label = readout.parentElement.querySelector('span')
			if (field && label) label.textContent = field.label
			// OBA does not report a gain setpoint; do not manufacture one.
			readout.parentElement.hidden = !field && !!sections.length
		}
		amplifierLiveFields = Array.isArray(result.live_fields) ? result.live_fields : []
		const gainField = xmlFields.find((field) => field.role === 'gain')
		const primaryMetric = document.querySelector('.metric-primary')
		setVariableTarget(primaryMetric, gainField)
		primaryMetric.hidden =
			!gainField || !amplifierLiveFields.includes(fieldIdentifier(gainField))
		const pinnedContainer = document.getElementById('amp-pinned-metrics')
		const pinnedFields = amplifierLiveFields
			.map((identifier) => xmlFields.find((field) => fieldIdentifier(field) === identifier))
			.filter((field) => field && field.role !== 'gain')
		pinnedContainer.innerHTML = pinnedFields
			.map(
				(field) =>
					`<div class="metric-item" ${variableDataAttributes(field)}><span>${escapeHtml(field.label)}</span><strong>${escapeHtml(measurementText(snapshot, field))}</strong></div>`,
			)
			.join('')
	} else {
		document.getElementById('device-live-board').innerHTML =
			sections
				.map((section) => {
					const groups = [...new Set(section.fields.map((field) => field.group))]
					return `<section class="fts-station-group"><div class="fts-section-heading"><h3>${escapeHtml(section.label)}</h3>${section.present ? '' : '<span>Not present</span>'}</div><div class="fts-station-systems">${groups
						.map((group) => {
							const fields = xmlFields.filter(
								(field) => field.section === section.key && field.group === group,
							)
							const switchedOff = fields
								.filter((field) => field.role === 'on')
								.some((field) => [false, 0].includes(fieldValue(snapshot, field)))
							return `<article class="fts-module${switchedOff ? ' is-switched-off' : ''}"><div class="fts-module-title"><strong>${escapeHtml(group)}</strong></div><dl class="fts-metrics">${fields.map((field) => `<div ${variableDataAttributes(field)}><dt>${escapeHtml(field.label)}</dt><dd>${escapeHtml(measurementText(snapshot, field))}</dd></div>`).join('')}</dl></article>`
						})
						.join('')}</div></section>`
				})
				.join('') || '<p>Waiting for station data...</p>'
	}
	document.getElementById('xml-live').innerHTML = sections
		.map(
			(section) =>
				`<article><h3>${escapeHtml(section.label)}</h3><div class="xml-metrics">${xmlFields
					.filter((f) => f.section === section.key)
					.map((field) => {
						const identifier = fieldIdentifier(field)
						const pinned = amplifierLiveFields.includes(identifier)
						const contents = `<span class="xml-metric-label">${escapeHtml(field.label)}</span><strong>${escapeHtml(measurementText(snapshot, field))}</strong>`
						return deviceProfile === 'amplifier' && canOperate()
							? `<button type="button" class="xml-metric xml-metric-selectable${pinned ? ' is-pinned' : ''}" data-live-field="${escapeHtml(identifier)}" ${variableDataAttributes(field)} aria-pressed="${pinned}">${contents}</button>`
							: `<div class="xml-metric${pinned ? ' is-pinned' : ''}" ${variableDataAttributes(field)}>${contents}</div>`
					})
					.join('')}</div></article>`,
		)
		.join('')
	const signature = JSON.stringify([xmlFields, xmlChartLayout])
	if (signature !== xmlLayoutSignature) {
		for (const chart of xmlCharts.values()) chart.destroy()
		xmlCharts.clear()
		xmlChartGroups = [...new Set(Object.values(xmlChartLayout))]
			.filter((chart) => Number.isInteger(chart) && chart >= 1 && chart <= 8)
			.sort((a, b) => a - b)
		document.getElementById('xml-charts').innerHTML = xmlChartGroups
			.map(
				(chart, index) =>
					`<article class="chart-card"><div class="chart-card-header"><h3>Chart ${chart}</h3><button class="chart-expand-button" type="button" aria-label="Expand chart"></button></div><div class="chart-container"><canvas id="xml-chart-${index}"></canvas></div></article>`,
			)
			.join('')
		setupChartExpansion()
		xmlLayoutSignature = signature
		lastOverviewChartRefresh = 0
	}
}

async function toggleAmplifierLiveField(identifier) {
	if (!canOperate() || deviceProfile !== 'amplifier') return
	const fields = amplifierLiveFields.includes(identifier)
		? amplifierLiveFields.filter((field) => field !== identifier)
		: [...amplifierLiveFields, identifier]
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/live-fields`,
			{
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ fields }),
			},
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not save live view'))
		amplifierLiveFields = result.live_fields
		showNotification('Live view updated for all users.')
		await updateDashboard()
	} catch (error) {
		showNotification(error.message || 'Could not save live view.', 'error')
	}
}

async function saveChartLayout() {
	if (!canOperate()) return
	const charts = {}
	for (const select of document.querySelectorAll('[data-chart-field]')) {
		const chart = Number(select.value)
		if (chart) charts[select.dataset.chartField] = chart
	}
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/chart-layout`,
			{
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ charts }),
			},
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not save chart layout'))
		xmlChartLayout = result.chart_layout
		xmlLayoutSignature = ''
		document.getElementById('xml-chart-settings').open = false
		showNotification('Chart layout updated for all users.')
		await updateDashboard()
		await loadXmlHistory()
	} catch (error) {
		showNotification(error.message || 'Could not save chart layout.', 'error')
	}
}

function closeChartSettings() {
	renderChartSettings()
	document.getElementById('xml-chart-settings').open = false
}

async function updateDashboard() {
	if (!currentUser || xmlDashboardBusy) return
	xmlDashboardBusy = true
	try {
		const response = await fetch(`/api/devices/${encodeURIComponent(selectedDeviceId)}/latest`)
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`HTTP error ${response.status}`)
		const result = await response.json()
		renderXmlStatus(result)
		setTextIfExists('status-last-update', formatTime(result.last_update))
		setTextIfExists('status-system-time', formatTime(result.system_time))
		updateDatabaseStatus(result.database)
		const status = document.getElementById('status-connection')
		status.textContent = result.connected ? 'DATA CURRENT' : 'NO CURRENT DATA'
		status.className = result.connected ? 'status-ok' : 'status-error'
	} catch (error) {
		setTextIfExists('device-source-message', error.message)
		const status = document.getElementById('status-connection')
		status.textContent = 'API ERROR'
		status.className = 'status-error'
		document.getElementById('device-live-board').classList.add('xml-stale')
	} finally {
		xmlDashboardBusy = false
	}
}

async function loadXmlHistory() {
	if (!currentUser || xmlHistoryBusy) return
	xmlHistoryBusy = true
	try {
		const range = document.getElementById('xml-history-range').value
		document.getElementById('xml-export').href =
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/history/export.csv?range=${encodeURIComponent(range)}`
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/history?range=${encodeURIComponent(range)}`,
		)
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`History unavailable (${response.status})`)
		const result = await response.json()
		if (range !== document.getElementById('xml-history-range').value) return
		const points = result.points || []
		const times = points.map((p) => new Date(p.time).getTime())
		const now = Date.now()
		const bounds = {
			min: times.length ? Math.min(...times) : now - 300000,
			max: times.length ? Math.max(...times) : now,
		}
		if (bounds.max === bounds.min) bounds.min -= 1000
		const colors = ['#66d9ac', '#75b9ff', '#e7bc6d', '#cf92eb', '#ef8596', '#a5cb65']
		xmlChartGroups.forEach((chart, index) => {
			const fields = xmlFields.filter(
				(field) => xmlChartLayout[fieldIdentifier(field)] === chart,
			)
			const datasets = fields.map((field, i) => ({
				label: field.label + (field.unit ? ` [${field.unit}]` : ''),
				data: points.map((p) => {
					const v = fieldValue(p.snapshot, field)
					return typeof v === 'number' ? v : null
				}),
				borderColor: colors[i % colors.length],
				pointRadius: 0,
				spanGaps: false,
			}))
			const id = `xml-chart-${index}`
			xmlCharts.set(
				id,
				createOrUpdateChart(
					xmlCharts.get(id) || null,
					id,
					points,
					points.map((p) => formatDateTime(p.time)),
					datasets,
					'Value',
					bounds,
				),
			)
		})
		setTextIfExists(
			'xml-history-message',
			points.length
				? `${points.length} displayed observations; CSV includes full history`
				: 'No observations in this range',
		)
		lastOverviewChartRefresh = Date.now()
	} catch (error) {
		setTextIfExists('xml-history-message', error.message)
	} finally {
		xmlHistoryBusy = false
	}
}

async function loadXmlStatistics() {
	if (!currentUser || xmlStatisticsBusy) return
	xmlStatisticsBusy = true
	try {
		const range = document.getElementById('xml-statistics-range').value
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/statistics?range=${encodeURIComponent(range)}`,
		)
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`Statistics unavailable (${response.status})`)
		const result = await response.json()
		if (range !== document.getElementById('xml-statistics-range').value) return
		const rows = Object.entries(result.statistics || {}).filter(([key]) =>
			key.startsWith('values.'),
		)
		document.getElementById('xml-statistics').innerHTML = rows.length
			? `<div class="table-wrap"><table class="plain-table"><thead><tr><th>Measurement</th><th>Count</th><th>Min</th><th>Max</th><th>Average</th><th>Standard deviation</th></tr></thead><tbody>${rows.map(([key, stats]) => `<tr><td>${escapeHtml(xmlFields.find((f) => f.path === key)?.title || key)}</td>${['count', 'min', 'max', 'average', 'standard_deviation'].map((name) => `<td>${escapeHtml(formatPlainNumber(stats[name], name === 'count' ? 0 : 2))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
			: '<p>No stored observations.</p>'
		lastStatisticsRefresh = Date.now()
	} catch (error) {
		setTextIfExists('xml-statistics', error.message)
	} finally {
		xmlStatisticsBusy = false
	}
}

document.getElementById('xml-history-range').addEventListener('change', () => {
	lastOverviewChartRefresh = 0
	loadXmlHistory()
})
document.getElementById('xml-statistics-range').addEventListener('change', () => {
	lastStatisticsRefresh = 0
	loadXmlStatistics()
})

const variableContextMenu = document.getElementById('variable-context-menu')
let contextVariable = null

function closeVariableContextMenu() {
	variableContextMenu.hidden = true
	contextVariable = null
}

document.addEventListener('contextmenu', (event) => {
	if (!isAdministrator()) return
	const target = event.target.closest('[data-variable-key]')
	if (!target) return
	event.preventDefault()
	contextVariable = {
		section: target.dataset.variableSection,
		key: target.dataset.variableKey,
		automatic: target.dataset.variableAutomatic === 'true',
	}
	variableContextMenu.querySelector('button').textContent = contextVariable.automatic
		? 'Add to mapping'
		: 'Edit variable'
	variableContextMenu.hidden = false
	const left = Math.min(event.clientX, window.innerWidth - variableContextMenu.offsetWidth - 8)
	const top = Math.min(event.clientY, window.innerHeight - variableContextMenu.offsetHeight - 8)
	variableContextMenu.style.left = `${Math.max(8, left)}px`
	variableContextMenu.style.top = `${Math.max(8, top)}px`
})

variableContextMenu.querySelector('button').addEventListener('click', () => {
	const variable = contextVariable
	closeVariableContextMenu()
	if (variable) window.openXmlVariableEditor(variable)
})

document.addEventListener('click', (event) => {
	if (!event.target.closest('#variable-context-menu')) closeVariableContextMenu()
})

window.addEventListener('blur', closeVariableContextMenu)

document.getElementById('xml-live').addEventListener('click', (event) => {
	const button = event.target.closest('[data-live-field]')
	if (button) toggleAmplifierLiveField(button.dataset.liveField)
})

document.getElementById('xml-save-chart-layout').addEventListener('click', saveChartLayout)
document.getElementById('xml-cancel-chart-layout').addEventListener('click', closeChartSettings)
document.getElementById('xml-chart-settings').addEventListener('toggle', (event) => {
	if (event.target.open) renderChartSettings()
})
