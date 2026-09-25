// Unified XML-backed controls for every field explicitly marked writable.
let deviceControlFields = []
const deviceControlDirty = new Set()
let deviceControlSignature = ''
let deviceControlRequestBusy = false

function controlInputValue(snapshot, field) {
	const value = fieldValue(snapshot, field)
	return value === null || value === undefined ? '' : String(value)
}

function controlRangeDescription(field) {
	if (field.minimum !== undefined && field.maximum !== undefined) {
		return `Allowed range: ${field.minimum}–${field.maximum}${field.unit ? ' ' + field.unit : ''}`
	}
	if (field.minimum !== undefined) return `Minimum: ${field.minimum}`
	if (field.maximum !== undefined) return `Maximum: ${field.maximum}`
	return 'Validated according to the XML mapping.'
}

function renderDeviceControl(snapshot, fields) {
	const container = document.getElementById('device-control-fields')
	if (!container) return
	deviceControlFields = fields.filter((field) => field.writable)
	const signature = JSON.stringify([
		selectedDeviceId,
		...deviceControlFields.map((field) => [
			fieldIdentifier(field),
			field.label,
			field.type,
			field.unit,
			field.minimum,
			field.maximum,
		]),
	])
	if (signature !== deviceControlSignature) {
		const groups = new Map()
		for (const field of deviceControlFields) {
			const key = `${field.section}:${field.group || 'Parameters'}`
			if (!groups.has(key)) {
				groups.set(key, {
					label: field.group || field.sectionLabel || 'Parameters',
					fields: [],
				})
			}
			groups.get(key).fields.push(field)
		}
		container.innerHTML = groups.size
			? Array.from(groups.values())
					.map(
						(group) =>
							`<section class="device-control-group"><h3>${escapeHtml(group.label)}</h3><div class="device-control-group-fields">${group.fields
								.map((field) => {
									const identifier = fieldIdentifier(field)
									const numeric = field.type === 'number'
									const attributes = [
										`data-control-field="${escapeHtml(identifier)}"`,
										`data-control-type="${escapeHtml(field.type)}"`,
										numeric ? 'type="number" step="any"' : 'type="text"',
										field.minimum !== undefined
											? `min="${escapeHtml(field.minimum)}"`
											: '',
										field.maximum !== undefined
											? `max="${escapeHtml(field.maximum)}"`
											: '',
										'data-operator-control',
									]
										.filter(Boolean)
										.join(' ')
									return `<label class="device-control-field"><span>${escapeHtml(field.label)}:</span><div class="device-control-input"><input ${attributes}><small>${escapeHtml(field.unit || '')}</small></div><small class="device-control-help">${escapeHtml(controlRangeDescription(field))}</small></label>`
								})
								.join('')}</div></section>`,
					)
					.join('')
			: '<p>No writable parameters are available for this device.</p>'
		deviceControlSignature = signature
		deviceControlDirty.clear()
	}
	for (const field of deviceControlFields) {
		const identifier = fieldIdentifier(field)
		const input = container.querySelector(`[data-control-field="${CSS.escape(identifier)}"]`)
		if (input && document.activeElement !== input && !deviceControlDirty.has(identifier)) {
			input.value = controlInputValue(snapshot, field)
		}
	}
	updateDeviceControlSubmitState()
}

function updateDeviceControlSubmitState() {
	const submit = document.getElementById('device-control-submit')
	if (submit)
		submit.disabled = !canOperate() || !deviceControlDirty.size || deviceControlRequestBusy
}

function renderDeviceControlStatus(result) {
	setTextIfExists('device-control-request-id', result.request_id || '--')
	const state = document.getElementById('device-control-state')
	if (state) {
		state.textContent = result.state || 'unknown'
		state.className = `control-state control-state-${result.state || 'unknown'}`
	}
	setTextIfExists('device-control-message', result.message || '--')
}

async function loadDeviceControlStatus() {
	if (!currentUser || !canOperate()) return
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/control/status`,
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not load device-control status'))
		renderDeviceControlStatus(result)
	} catch (error) {
		setTextIfExists('device-control-message', error.message || 'Could not load status.')
	}
}

async function submitDeviceControl(event) {
	event.preventDefault()
	if (!canOperate() || !deviceControlDirty.size || deviceControlRequestBusy) return
	const values = {}
	for (const identifier of deviceControlDirty) {
		const field = deviceControlFields.find((item) => fieldIdentifier(item) === identifier)
		const input = document.querySelector(`[data-control-field="${CSS.escape(identifier)}"]`)
		if (!field || !input) continue
		if (!input.reportValidity()) return
		if (field.type === 'number') {
			const value = Number(input.value)
			if (!Number.isFinite(value)) {
				showNotification(`${field.label}: enter a finite number.`, 'error')
				return
			}
			values[identifier] = value
		} else {
			values[identifier] = input.value
		}
	}
	deviceControlRequestBusy = true
	updateDeviceControlSubmitState()
	try {
		const response = await fetch(
			`/api/devices/${encodeURIComponent(selectedDeviceId)}/control`,
			{
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ values }),
			},
		)
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok)
			throw new Error(apiErrorMessage(result.detail, 'Could not write device control'))
		deviceControlDirty.clear()
		renderDeviceControlStatus({
			request_id: result.request_id,
			state: result.state,
			message: 'Awaiting acknowledgement from status.xml.',
		})
		showNotification('Control request written. Waiting for device acknowledgement.')
	} catch (error) {
		showNotification(error.message || 'Could not write device control.', 'error')
	} finally {
		deviceControlRequestBusy = false
		updateDeviceControlSubmitState()
	}
}

document.getElementById('device-control-fields')?.addEventListener('input', (event) => {
	const identifier = event.target.dataset.controlField
	if (!identifier) return
	deviceControlDirty.add(identifier)
	updateDeviceControlSubmitState()
})
document.getElementById('device-control-form')?.addEventListener('submit', submitDeviceControl)
document
	.getElementById('device-control-refresh')
	?.addEventListener('click', loadDeviceControlStatus)
