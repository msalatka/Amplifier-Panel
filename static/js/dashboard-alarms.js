let alarmSettingsSignature = ''

function renderAlarmSettings(_snapshot, fields) {
	const container = document.getElementById('alarm-settings-fields')
	if (!container) return
	const numeric = fields.filter((field) => field.type === 'number' && !field.automatic)
	const signature = JSON.stringify(
		numeric.map((field) => [fieldIdentifier(field), field.label, field.group, field.alarm]),
	)
	if (signature === alarmSettingsSignature) return
	const groups = new Map()
	for (const field of numeric) {
		const group = `${field.section}:${field.group || 'Measurements'}`
		if (!groups.has(group))
			groups.set(group, { label: field.group || 'Measurements', fields: [] })
		groups.get(group).fields.push(field)
	}
	container.innerHTML = Array.from(groups.values())
		.map(
			(group) =>
				`<section class="alarm-settings-group"><h3>${escapeHtml(group.label)}</h3>${group.fields
					.map((field) => {
						const alarm = field.alarm || { enabled: false }
						return `<div class="alarm-setting-row" data-alarm-field="${escapeHtml(fieldIdentifier(field))}"><label class="toggle-field"><input data-alarm-enabled type="checkbox"${alarm.enabled ? ' checked' : ''}>${escapeHtml(field.label)}</label><label>MIN<input data-alarm-minimum type="number" step="any" value="${alarm.minimum ?? ''}"></label><label>MAX<input data-alarm-maximum type="number" step="any" value="${alarm.maximum ?? ''}"></label><span>${escapeHtml(field.unit || '')}</span></div>`
					})
					.join('')}</section>`,
		)
		.join('')
	alarmSettingsSignature = signature
}

async function loadActiveAlarms() {
	if (!currentUser) return
	try {
		const response = await fetch(`/api/devices/${encodeURIComponent(selectedDeviceId)}/alarms`)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok) throw new Error(apiErrorMessage(result.detail, 'Could not load alarms'))
		const alarms = result.alarms || []
		setTextIfExists('active-alarm-count', String(alarms.length))
		const container = document.getElementById('active-alarms-list')
		container.innerHTML = alarms.length
			? alarms
					.map((alarm) => {
						const state = alarm.condition_active
							? alarm.acknowledged
								? 'Active · acknowledged'
								: 'Active · unacknowledged'
							: 'Normal · acknowledgement required'
						const action = canOperate()
							? alarm.acknowledged
								? 'Acknowledged'
								: `<button class="button-secondary" type="button" data-acknowledge-alarm="${escapeHtml(alarm.key)}">Acknowledge</button>`
							: '--'
						return `<div class="alarm-active-row"><span><strong>${escapeHtml(alarm.label)}</strong><small>${escapeHtml(alarm.kind === 'minimum' ? 'Value below configured minimum' : 'Value above configured maximum')}</small></span><strong>${escapeHtml(`${alarm.value}${alarm.unit ? ` ${alarm.unit}` : ''}`)}</strong><span>${escapeHtml(alarm.kind === 'minimum' ? `Minimum: ${alarm.target}` : `Maximum: ${alarm.target}`)}${escapeHtml(alarm.unit ? ` ${alarm.unit}` : '')}</span><span class="alarm-state${alarm.condition_active ? ' is-active' : ' is-normal'}">${escapeHtml(state)}</span><time datetime="${escapeHtml(alarm.opened_at || '')}">${escapeHtml(formatDateTime(alarm.opened_at))}</time><span>${action}</span></div>`
					})
					.join('')
			: '<p>No active alarms.</p>'
	} catch (error) {
		showNotification(error.message || 'Could not load alarms.', 'error')
	}
}

document.getElementById('alarm-settings-form')?.addEventListener('submit', async (event) => {
	event.preventDefault()
	const alarms = {}
	for (const row of document.querySelectorAll('[data-alarm-field]')) {
		const minimum = row.querySelector('[data-alarm-minimum]').value
		const maximum = row.querySelector('[data-alarm-maximum]').value
		alarms[row.dataset.alarmField] = {
			enabled: row.querySelector('[data-alarm-enabled]').checked,
			minimum: minimum === '' ? null : Number(minimum),
			maximum: maximum === '' ? null : Number(maximum),
		}
	}
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/alarms/settings`,
			{
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ alarms }),
			},
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not save alarm settings'))
		showNotification('Alarm settings saved.')
	} catch (error) {
		showNotification(error.message || 'Could not save alarm settings.', 'error')
	}
})
document.getElementById('refresh-alarms-button')?.addEventListener('click', loadActiveAlarms)
document.getElementById('active-alarms-list')?.addEventListener('click', async (event) => {
	const button = event.target.closest('[data-acknowledge-alarm]')
	if (!button || !canOperate()) return
	button.disabled = true
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/alarms/acknowledge`,
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ key: button.dataset.acknowledgeAlarm }),
			},
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not acknowledge alarm'))
		await loadActiveAlarms()
	} catch (error) {
		button.disabled = false
		showNotification(error.message || 'Could not acknowledge alarm.', 'error')
	}
})
