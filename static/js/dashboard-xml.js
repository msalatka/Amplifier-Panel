// XML selectors and display metadata come from the editable server mapping.
let xmlChart = null
let xmlFields = []
let xmlHistoryBusy = false
let xmlOptionsSignature = ''

function renderXmlStatus(result) {
	const snapshot = result.data || {}
	const container = document.getElementById('xml-live')
	container.classList.toggle('xml-stale', !result.connected)
	const sections = snapshot.sections || []
	const module = snapshot.module || {}
	container.innerHTML =
		`<div class="data-panel"><h2>${escapeHtml(snapshot.label || selectedDeviceId)}</h2>
		<p>${escapeHtml(module.systemName || '')} · ${escapeHtml(module.systemType || '')}</p>
		<p>Source timestamp: ${escapeHtml(module.dataUpdate || '--')} · Last successful read: ${escapeHtml(result.last_update || '--')}</p>
		<p role="status">${escapeHtml(result.error || (result.connected ? 'Reading status.xml' : 'Waiting for status.xml'))}</p>
		${!result.connected && result.last_update ? '<p>Last known values — source unavailable or stale.</p>' : ''}</div>` +
		sections
			.map(
				(section) => `<article class="data-panel"><h2>${escapeHtml(section.label)}</h2>
		${!section.present ? '<p>Section not present in XML</p>' : ''}
		<div class="xml-metrics">${section.fields
			.map(
				(field) => `<div class="xml-metric"><span>${escapeHtml(field.label)}</span>
		<strong>${escapeHtml(snapshot.values?.[section.key]?.[field.key] ?? '--')} ${escapeHtml(field.unit)}</strong></div>`,
			)
			.join('')}</div></article>`,
			)
			.join('')
	xmlFields = sections.flatMap((section) =>
		section.fields.map((field) => ({
			...field,
			section: section.key,
			path: `values.${section.key}.${field.key}`,
			title: `${section.label} / ${field.label}`,
		})),
	)
	const select = document.getElementById('xml-history-field')
	const signature = JSON.stringify(xmlFields)
	if (signature !== xmlOptionsSignature) {
		const previous = select.value
		select.replaceChildren(...xmlFields.map((field) => new Option(field.title, field.path)))
		if (xmlFields.some((field) => field.path === previous)) select.value = previous
		xmlOptionsSignature = signature
	}
}

async function loadXmlHistory() {
	if (!currentUser || xmlHistoryBusy) return
	xmlHistoryBusy = true
	try {
		const range = document.getElementById('xml-history-range').value
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/history?range=${encodeURIComponent(range)}`,
		)
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`History unavailable (${response.status})`)
		const result = await response.json()
		const field = xmlFields.find(
			(item) => item.path === document.getElementById('xml-history-field').value,
		)
		const points = result.points || []
		const values = points.map((point) => {
			const value = point.snapshot?.values?.[field?.section]?.[field?.key]
			return typeof value === 'number' ? value : null
		})
		setTextIfExists(
			'xml-history-message',
			values.some((value) => value !== null)
				? `${points.length} observations (bounded history)`
				: 'No numeric observations for this field and range.',
		)
		if (xmlChart) xmlChart.destroy()
		xmlChart = new Chart(document.getElementById('xml-history-chart'), {
			type: 'line',
			data: {
				labels: points.map((point) => new Date(point.time).toLocaleString()),
				datasets: [
					{
						label: field?.title || 'Value',
						data: values,
						borderColor: '#167a8a',
						pointRadius: 0,
						spanGaps: false,
					},
				],
			},
			options: { responsive: true, maintainAspectRatio: false, animation: false },
		})
		lastOverviewChartRefresh = Date.now()
	} catch (error) {
		setTextIfExists('xml-history-message', error.message)
	} finally {
		xmlHistoryBusy = false
	}
}

async function loadXmlStatistics() {
	if (!currentUser) return
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/statistics?range=1h`,
		)
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`Statistics unavailable (${response.status})`)
		const result = await response.json()
		const rows = Object.entries(result.statistics || {}).filter(([key]) =>
			key.startsWith('values.'),
		)
		document.getElementById('xml-statistics').innerHTML = rows.length
			? `<div class="table-wrap"><table><thead><tr><th>Measurement</th><th>Count</th><th>Min</th><th>Max</th><th>Average</th></tr></thead><tbody>${rows.map(([key, stats]) => `<tr><td>${escapeHtml(xmlFields.find((field) => field.path === key)?.title || key)}</td>${['count', 'min', 'max', 'average'].map((name) => `<td>${escapeHtml(formatPlainNumber(stats[name]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
			: '<p>No stored observations.</p>'
		lastStatisticsRefresh = Date.now()
	} catch (error) {
		setTextIfExists('xml-statistics', error.message)
	}
}

if (deviceProfile === 'xml') {
	document.getElementById('xml-history-field').addEventListener('change', loadXmlHistory)
	document.getElementById('xml-history-range').addEventListener('change', loadXmlHistory)
}
