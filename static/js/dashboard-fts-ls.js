// FTS-LS controls, history normalization and station-specific charts.
function selectedFtsModule() {
	const target = document.getElementById('fts-target')?.value || 'ul'
	if (!latestFtsStatus) return null
	if (target === 'ul') return latestFtsStatus.uplink || null
	const match = target.match(/^port([1-7])$/)
	return match ? (latestFtsStatus.ports || [])[Number(match[1]) - 1] : null
}

function setFtsInputValue(id, value, force = false) {
	const input = document.getElementById(id)
	if (!input || (!force && ftsDirtyInputs.has(id)) || document.activeElement === input) return
	if (input.type === 'number') input.value = ftsNumeric(value) ?? ''
	else if (value === true) input.value = 'true'
	else if (value === false) input.value = 'false'
	else input.value = value ?? ''
	ftsInputBaselines.set(id, input.value)
	if (force) ftsDirtyInputs.delete(id)
}

function updateFtsFormState(form) {
	if (!form) return
	const dirty = [...form.querySelectorAll('[data-fts-setting-action]')].some((input) =>
		ftsDirtyInputs.has(input.id),
	)
	form.classList.toggle('dirty', dirty)
	const save = form.querySelector('.fts-save-settings')
	if (save) save.disabled = deviceProfile === 'fts-ls' || !dirty || !canOperate()
}

function clearFtsFormChanges(form) {
	form?.querySelectorAll('[data-fts-setting-action]').forEach((input) => {
		ftsDirtyInputs.delete(input.id)
	})
	updateFtsFormState(form)
}

function updateFtsTargetForm(force = false) {
	if (deviceProfile !== 'fts-ls') return
	const target = document.getElementById('fts-target')?.value || 'ul'
	if (!force && ftsFormTarget === target && !latestFtsStatus) return
	const module = selectedFtsModule()
	if (!module) return
	ftsFormTarget = target
	const assignments = {
		'fts-description-input': firstValue(module, ['description'], ''),
		'fts-optical-power-input':
			String(firstValue(module, ['state'], '')).toUpperCase() !== 'SHUTDOWN',
		'fts-distance-input': firstValue(module, ['distance_km'], ''),
		'fts-gain-input': firstValue(module, ['additional_gain_db'], 0),
		'fts-pol-power-input': firstValue(module, ['polarization_control'], true),
		'fts-pol-speed-input': String(
			firstValue(module, ['polarization_controller_speed'], 'fast'),
		).toLowerCase(),
		'fts-pol-mode-input': String(
			firstValue(module, ['polarization_controller_mode'], 'continuous'),
		).toLowerCase(),
	}
	Object.entries(assignments).forEach(([id, value]) => {
		setFtsInputValue(id, value, force)
	})

	const type = String(
		firstValue(module, ['type'], target === 'ul' ? 'Uplink' : 'Unknown'),
	).toLowerCase()
	const isDownlink = type.includes('downlink')
	const hasPolarization = target === 'ul' || type.includes('feedback')
	document.querySelectorAll('[data-fts-downlink-only]').forEach((field) => {
		field.hidden = !isDownlink
		field.querySelectorAll('input, select').forEach((input) => {
			input.disabled = !isDownlink
		})
	})
	document.querySelectorAll('[data-fts-polarization-only]').forEach((field) => {
		field.hidden = !hasPolarization
		field.querySelectorAll('input, select').forEach((input) => {
			input.disabled = !hasPolarization
		})
	})
	document.querySelectorAll('[data-fts-setting-action]').forEach((input) => {
		input.disabled = true
	})
	setTextIfExists(
		'fts-port-settings-hint',
		`${target === 'ul' ? 'UL' : target.toUpperCase()} · ${firstValue(module, ['type'], 'Unknown')}`,
	)
	updateFtsFormState(document.getElementById('fts-port-settings-form'))
}

function updateFtsSettingsForms(force = false) {
	if (!latestFtsStatus) return
	const laser = latestFtsStatus.laser || {}
	const tec = latestFtsStatus.tec || {}
	const synth = latestFtsStatus.synth || {}
	const laserState = String(firstValue(laser, ['state'], 'ON')).toUpperCase()
	setFtsInputValue('fts-laser-power', !['OFF', 'SHUTDOWN', 'FALSE'].includes(laserState), force)
	setFtsInputValue(
		'fts-laser-mode',
		String(firstValue(laser, ['mode'], 'normal'))
			.toLowerCase()
			.replaceAll('_', '-'),
		force,
	)
	setFtsInputValue(
		'fts-laser-frequency-input',
		firstValue(laser, ['central_frequency_set'], ''),
		force,
	)
	setFtsInputValue(
		'fts-laser-span-input',
		firstValue(laser, ['scanning_frequency_span_set'], ''),
		force,
	)
	const tecState = String(firstValue(tec, ['state'], 'ON')).toUpperCase()
	setFtsInputValue('fts-tec-power-input', !['OFF', 'FALSE'].includes(tecState), force)
	setFtsInputValue('fts-tec-temperature-input', firstValue(tec, ['temperature_set_c'], ''), force)
	setFtsInputValue(
		'fts-reference-input',
		firstValue(synth, ['external_frequency_allowed'], true),
		force,
	)
	updateFtsTargetForm(force)
	document.querySelectorAll('.fts-settings-form').forEach(updateFtsFormState)
}

const ftsTransferActions = new Set([
	'power_reset',
	'factory_default',
	'laser_power',
	'laser_central_frequency',
	'laser_mode',
	'laser_force_relock',
	'optical_power',
])

async function postFtsAction(action, parameters, confirmed = false) {
	const response = await fetch('/api/fts-ls/command', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ action, parameters, confirmed }),
	})
	handleAuthResponse(response)
	if (!response.ok) throw await responseError(response, 'FTS-LS command failed')
	return response.json()
}

async function sendFtsAction(button) {
	const action = button.dataset.ftsAction
	const parameters = {}
	if (button.hasAttribute('data-fts-target'))
		parameters.target = document.getElementById('fts-target')?.value
	if (button.dataset.ftsValueFrom)
		parameters.value = document.getElementById(button.dataset.ftsValueFrom)?.value
	if (button.dataset.ftsEnabledFrom)
		parameters.enabled =
			document.getElementById(button.dataset.ftsEnabledFrom)?.value === 'true'
	let confirmed = false
	if (ftsTransferActions.has(action) || ['reboot'].includes(action)) {
		confirmed = window.confirm(
			'This operation may interrupt frequency transfer or restart the station. Continue?',
		)
		if (!confirmed) return
	}
	button.disabled = true
	try {
		await postFtsAction(action, parameters, confirmed)
		showNotification('FTS-LS command accepted.')
		ftsFormTarget = null
		window.setTimeout(updateDashboard, 500)
	} catch (error) {
		showNotification(error.message || 'FTS-LS command failed.', 'error')
	} finally {
		button.disabled = false
	}
}

async function saveFtsSettings(form) {
	const inputs = [...form.querySelectorAll('[data-fts-setting-action]')].filter(
		(input) => ftsDirtyInputs.has(input.id) && !input.disabled,
	)
	if (!inputs.length) return
	const transferAffecting = inputs.some((input) =>
		ftsTransferActions.has(input.dataset.ftsSettingAction),
	)
	if (
		transferAffecting &&
		!window.confirm(
			'These changes may interrupt frequency transfer for several minutes. Continue?',
		)
	)
		return
	const save = form.querySelector('.fts-save-settings')
	if (save) save.disabled = true
	let completed = 0
	try {
		for (const input of inputs) {
			const parameters = {}
			if (input.hasAttribute('data-fts-target'))
				parameters.target = document.getElementById('fts-target')?.value
			if (input.dataset.ftsValueKind === 'enabled')
				parameters.enabled = input.value === 'true'
			else parameters.value = input.value
			await postFtsAction(input.dataset.ftsSettingAction, parameters, transferAffecting)
			ftsInputBaselines.set(input.id, input.value)
			ftsDirtyInputs.delete(input.id)
			completed += 1
		}
		showNotification(`${completed} setting${completed === 1 ? '' : 's'} saved.`)
		ftsFormTarget = null
		window.setTimeout(updateDashboard, 500)
	} catch (error) {
		showNotification(
			`${completed} of ${inputs.length} settings saved. ${error.message || 'The next command failed.'}`,
			'error',
		)
	} finally {
		updateFtsFormState(form)
	}
}

function setupFtsControls() {
	if (deviceProfile !== 'fts-ls') return
	// The station transport is intentionally read-only until its daemon XML contract exists.
	document.querySelectorAll('[data-fts-setting-action], [data-fts-action], .fts-save-settings, .fts-cancel-settings').forEach((control) => {
		control.disabled = true
	})
	document.querySelectorAll('.fts-settings-form').forEach((form) => {
		updateFtsFormState(form)
		form.querySelectorAll('[data-fts-setting-action]').forEach((input) => {
			const markDirty = () => {
				if (input.value === ftsInputBaselines.get(input.id)) ftsDirtyInputs.delete(input.id)
				else ftsDirtyInputs.add(input.id)
				updateFtsFormState(form)
			}
			ftsInputBaselines.set(input.id, input.value)
			input.addEventListener('input', markDirty)
			input.addEventListener('change', markDirty)
		})
		form.addEventListener('submit', (event) => {
			event.preventDefault()
			saveFtsSettings(form)
		})
		form.querySelector('.fts-cancel-settings')?.addEventListener('click', () => {
			clearFtsFormChanges(form)
			updateFtsSettingsForms()
		})
	})
	document.getElementById('fts-target')?.addEventListener('change', (event) => {
		const form = document.getElementById('fts-port-settings-form')
		const hasChanges = [...form.querySelectorAll('[data-fts-setting-action]')].some((input) =>
			ftsDirtyInputs.has(input.id),
		)
		if (hasChanges && !window.confirm('Discard unsaved changes for the selected module?')) {
			event.target.value = ftsFormTarget || 'ul'
			event.preventDefault()
			return
		}
		clearFtsFormChanges(form)
		ftsFormTarget = null
		updateFtsTargetForm(true)
		highlightSelectedFtsModule()
	})
	const moduleRack = document.getElementById('fts-modules')
	const uplinkRack = document.getElementById('fts-uplink')
	const selectModule = (event) => {
		if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return
		const module = event.target.closest('[data-fts-module-target]:not(.unequipped)')
		if (!module || !(moduleRack?.contains(module) || uplinkRack?.contains(module))) return
		if (event.type === 'keydown') event.preventDefault()
		const select = document.getElementById('fts-target')
		if (!select) return
		const requestedTarget = module.dataset.ftsModuleTarget
		select.value = requestedTarget
		const accepted = select.dispatchEvent(
			new Event('change', { bubbles: true, cancelable: true }),
		)
		if (!accepted || select.value !== requestedTarget) return
		highlightSelectedFtsModule()
		if (!setActiveTab('device-settings')) return
		history.replaceState(
			null,
			'',
			`${window.location.pathname}${window.location.search}#device-settings`,
		)
		window.requestAnimationFrame(() => {
			document
				.getElementById('fts-port-settings-form')
				?.scrollIntoView({ behavior: 'smooth', block: 'start' })
		})
	}
	moduleRack?.addEventListener('click', selectModule)
	moduleRack?.addEventListener('keydown', selectModule)
	uplinkRack?.addEventListener('click', selectModule)
	uplinkRack?.addEventListener('keydown', selectModule)
	document.querySelectorAll('[data-fts-action]').forEach((button) => {
		button.addEventListener('click', () => sendFtsAction(button))
	})
}

function ftsNumeric(value) {
	if (value === null || value === undefined || value === '') return null
	const numeric = Number(value)
	if (Number.isFinite(numeric)) return numeric
	const match = String(value).match(/[-+]?\d+(?:[.,]\d+)?/)
	return match ? Number(match[0].replace(',', '.')) : null
}

/** Convert a nested snapshot into the flat numeric shape consumed by Chart.js.
 * @param {{time: string, snapshot: FtsStatus}} point
 * @returns {Object<string, number|string|null>}
 */
function flattenFtsHistoryPoint(point) {
	const snapshot = point.snapshot || {}
	const flattened = { time: point.time }
	const laser = snapshot.laser || {}
	const tec = snapshot.tec || {}
	flattened.laser_frequency = ftsNumeric(firstValue(laser, ['optical_frequency']))
	flattened.tec_set = ftsNumeric(firstValue(tec, ['temperature_set_c']))
	flattened.tec_read = ftsNumeric(firstValue(tec, ['temperature_read_c']))
	for (const module of [snapshot.uplink || {}, ...(snapshot.ports || [])]) {
		const name = String(module.name || '').toUpperCase()
		if (!['UL', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7'].includes(name)) continue
		flattened[`${name}_power`] = ftsNumeric(module.optical_power)
		flattened[`${name}_noise_lf`] = ftsNumeric(module.noise_lf)
		flattened[`${name}_noise_hf`] = ftsNumeric(module.noise_hf)
		flattened[`${name}_jitter`] = ftsNumeric(module.jitter)
	}
	return flattened
}

function ftsTimeBounds(points, range) {
	const durations = {
		'5m': 5 * 60 * 1000,
		'1h': 60 * 60 * 1000,
		'24h': 24 * 60 * 60 * 1000,
		'7d': 7 * 24 * 60 * 60 * 1000,
		'30d': 30 * 24 * 60 * 60 * 1000,
	}
	const times = points.map((point) => new Date(point.time).getTime()).filter(Number.isFinite)
	if (range !== 'all') {
		const max = Date.now()
		return { min: max - durations[range], max }
	}
	if (times.length) return { min: times[0], max: times.at(-1) }
	const max = Date.now()
	return { min: max - 60 * 60 * 1000, max }
}

function ftsModuleDatasets(points, suffix, names) {
	return names.map((name) => ({
		label: name,
		data: getValues(points, `${name}_${suffix}`),
		spanGaps: false,
	}))
}

function updateFtsHistoryCharts(points, range) {
	const chartPoints = addHistoryGapMarkers(points.map(flattenFtsHistoryPoint), range)
	const timestamps = getFullTimestamps(chartPoints)
	const bounds = ftsTimeBounds(chartPoints, range)
	const modules = ['UL', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7']
	const ports = modules.slice(1)
	ftsOpticalPowerChart = createOrUpdateChart(
		ftsOpticalPowerChart,
		'fts-optical-power-chart',
		chartPoints,
		timestamps,
		ftsModuleDatasets(chartPoints, 'power', modules),
		'Power [dBm]',
		bounds,
	)
	ftsLfNoiseChart = createOrUpdateChart(
		ftsLfNoiseChart,
		'fts-lf-noise-chart',
		chartPoints,
		timestamps,
		ftsModuleDatasets(chartPoints, 'noise_lf', modules),
		'LF noise [a.u.]',
		bounds,
	)
	ftsHfNoiseChart = createOrUpdateChart(
		ftsHfNoiseChart,
		'fts-hf-noise-chart',
		chartPoints,
		timestamps,
		ftsModuleDatasets(chartPoints, 'noise_hf', modules),
		'HF noise [a.u.]',
		bounds,
	)
	ftsJitterChart = createOrUpdateChart(
		ftsJitterChart,
		'fts-jitter-chart',
		chartPoints,
		timestamps,
		ftsModuleDatasets(chartPoints, 'jitter', ports),
		'Jitter [%]',
		bounds,
	)
	ftsLaserFrequencyChart = createOrUpdateChart(
		ftsLaserFrequencyChart,
		'fts-laser-frequency-chart',
		chartPoints,
		timestamps,
		[{ label: 'Laser', data: getValues(chartPoints, 'laser_frequency'), spanGaps: false }],
		'Frequency [GHz]',
		bounds,
	)
	ftsTecChart = createOrUpdateChart(
		ftsTecChart,
		'fts-tec-chart',
		chartPoints,
		timestamps,
		[
			{ label: 'Setpoint', data: getValues(chartPoints, 'tec_set'), spanGaps: false },
			{ label: 'Measured', data: getValues(chartPoints, 'tec_read'), spanGaps: false },
		],
		'Temperature [°C]',
		bounds,
	)
}

async function loadFtsOverview() {
	if (deviceProfile !== 'fts-ls' || !currentUser) return
	const requestSequence = ++overviewRequestSequence
	if (overviewRequestController) overviewRequestController.abort()
	overviewRequestController = new AbortController()
	const requestController = overviewRequestController
	lastOverviewChartRefresh = Date.now()
	setTextIfExists('fts-overview-loading', 'Loading…')
	try {
		const response = await fetch(`/api/fts-ls/history?${buildOverviewQuery()}&limit=10000`, {
			signal: requestController.signal,
		})
		handleAuthResponse(response)
		if (!response.ok) throw await responseError(response, 'Could not load FTS-LS history')
		const result = await response.json()
		if (requestSequence !== overviewRequestSequence) return
		updateFtsHistoryCharts(result.points || [], overviewRange)
		setTextIfExists('fts-overview-loading', '')
		lastOverviewChartRefresh = Date.now()
	} catch (error) {
		if (error.name === 'AbortError' || requestSequence !== overviewRequestSequence) return
		setTextIfExists('fts-overview-loading', 'Could not load data')
		console.error('Error fetching FTS-LS history:', error)
	} finally {
		if (requestSequence === overviewRequestSequence) overviewRequestController = null
	}
}

function ftsStatisticsFields() {
	const modules = ['UL', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7']
	const fields = []
	modules.forEach((name) => {
		const metricPrefix = name === 'UL' ? 'uplink' : `ports.${name}`
		fields.push({
			key: `${name}_power`,
			metricKey: `${metricPrefix}.optical_power`,
			label: `${name} Optical Power`,
			unit: 'dBm',
		})
		fields.push({
			key: `${name}_noise_lf`,
			metricKey: `${metricPrefix}.noise_lf`,
			label: `${name} Low-frequency Noise`,
			unit: '',
		})
		fields.push({ key: `${name}_noise_hf`, metricKey: `${metricPrefix}.noise_hf`, label: `${name} High-frequency Noise`, unit: '' })
		if (name !== 'UL')
			fields.push({ key: `${name}_jitter`, metricKey: `${metricPrefix}.jitter`, label: `${name} Jitter`, unit: '%' })
	})
	fields.push({ key: 'laser_frequency', metricKey: 'laser.optical_frequency', label: 'Laser Frequency', unit: 'GHz' })
	fields.push({ key: 'tec_set', metricKey: 'tec.temperature_set_c', label: 'TEC Setpoint', unit: '°C' })
	fields.push({ key: 'tec_read', metricKey: 'tec.temperature_read_c', label: 'TEC Temperature', unit: '°C' })
	return fields
}

async function loadFtsStatistics() {
	if (deviceProfile !== 'fts-ls' || !currentUser) return
	const requestSequence = ++statisticsRequestSequence
	if (statisticsRequestController) statisticsRequestController.abort()
	statisticsRequestController = new AbortController()
	const requestController = statisticsRequestController
	lastStatisticsRefresh = Date.now()
	setTextIfExists('fts-statistics-source', 'Loading…')
	try {
		const response = await fetch(`/api/devices/${encodeURIComponent(selectedDeviceId)}/statistics?${buildStatisticsQuery()}`, {
			signal: requestController.signal,
		})
		handleAuthResponse(response)
		if (!response.ok) throw await responseError(response, 'Could not load FTS-LS statistics')
		const result = await response.json()
		if (requestSequence !== statisticsRequestSequence) return
		const metrics = result.statistics || {}
		const rows = ftsStatisticsFields()
			.map((field) => [field, metrics[field.metricKey]])
			.filter(([, statistics]) => statistics)
		const body = document.getElementById('fts-statistics-body')
		if (body) {
			body.innerHTML = rows.length
				? rows
						.map(([field, statistics]) => {
							const unit = field.unit ? ` ${field.unit}` : ''
							const outside =
								field.min === undefined && field.max === undefined
									? '--'
									: statistics.outside
										? `<span class="status-error">${statistics.outside}</span>`
										: '<span class="status-ok">0</span>'
							return (
								`<tr><td>${escapeHtml(field.label)}</td><td>${statistics.count}</td>` +
								`<td>${formatPlainNumber(statistics.min, 3)}${unit}</td>` +
								`<td>${formatPlainNumber(statistics.max, 3)}${unit}</td>` +
								`<td>${formatPlainNumber(statistics.average, 3)}${unit}</td>` +
								`<td>${formatPlainNumber(statistics.standard_deviation, 3)}${unit}</td><td>${outside}</td></tr>`
							)
						})
						.join('')
				: '<tr><td colspan="7">No numeric data in this range</td></tr>'
		}
		const rangeText = statisticsStart || statisticsEnd ? 'custom range' : statisticsRange
		setTextIfExists('fts-statistics-source', `${result.sample_count || 0} snapshots, ${rangeText}`)
		lastStatisticsRefresh = Date.now()
	} catch (error) {
		if (error.name === 'AbortError' || requestSequence !== statisticsRequestSequence) return
		setTextIfExists('fts-statistics-source', 'Could not load data')
		const body = document.getElementById('fts-statistics-body')
		if (body)
			body.innerHTML = `<tr><td colspan="7">${escapeHtml(error.message || 'Statistics unavailable')}</td></tr>`
	} finally {
		if (requestSequence === statisticsRequestSequence) statisticsRequestController = null
	}
}
