// Network, time and service-diagnostics UI.
function selectedNetworkInterface() {
	const name = document.getElementById('network-interface')?.value
	return latestNetwork?.interfaces?.find((item) => item.name === name) || null
}

function showNetworkMessage(message, isError = false) {
	const element = document.getElementById('network-message')
	if (!element) return
	element.textContent = message
	element.classList.toggle('error', isError)
}

function networkMdnsUrl(network = latestNetwork) {
	const hostname = network?.mdns_hostname
	if (!hostname) return ''
	const port = window.location.port ? `:${window.location.port}` : ''
	return `${window.location.protocol}//${hostname}${port}`
}

function networkOriginUsesHostname() {
	const hostname = window.location.hostname.toLowerCase().replace(/\.$/, '')
	if (
		hostname === 'localhost' ||
		hostname === '127.0.0.1' ||
		hostname === '::1' ||
		hostname === '[::1]'
	)
		return true
	const isIpv4 = /^(?:\d{1,3}\.){3}\d{1,3}$/.test(hostname)
	const isIpv6 = hostname.includes(':')
	return !isIpv4 && !isIpv6
}

function wait(milliseconds) {
	return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function confirmNetworkChangeWithRetry(token, rollbackSeconds) {
	const retryWindowSeconds = Math.max(5, Number(rollbackSeconds || 60) - 10)
	const deadline = Date.now() + retryWindowSeconds * 1000
	let lastError = new Error('The new network address did not become reachable.')

	while (Date.now() < deadline) {
		const controller = new AbortController()
		const timeout = window.setTimeout(() => controller.abort(), 4000)
		try {
			const response = await fetch('/api/network/confirm', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ token }),
				cache: 'no-store',
				signal: controller.signal,
			})
			handleAuthResponse(response)
			const data = await response.json()
			if (response.ok) return data
			if ([400, 401, 403].includes(response.status)) {
				const rejection = new Error(
					apiErrorMessage(data.detail, 'Network confirmation was rejected.'),
				)
				rejection.retryable = false
				throw rejection
			}
			lastError = new Error(
				apiErrorMessage(
					data.detail,
					`Network confirmation failed with HTTP ${response.status}.`,
				),
			)
		} catch (error) {
			lastError = error
			if (
				error.retryable === false ||
				['Not authenticated', 'Not allowed'].includes(error.message)
			)
				throw error
		} finally {
			window.clearTimeout(timeout)
		}
		await wait(1500)
	}

	throw new Error(
		`${lastError.message || 'The new address is unreachable.'} The previous network settings will be restored automatically.`,
	)
}

function updateNetworkStaticFields() {
	const form = document.getElementById('network-form')
	const container = document.getElementById('network-static-fields')
	if (!form || !container) return
	const isStatic = form.elements.mode.value === 'static'
	container.classList.toggle('is-expanded', isStatic)
	container.setAttribute('aria-hidden', String(!isStatic))
	container.querySelectorAll('input').forEach((input) => {
		input.disabled = !isStatic
		input.required = isStatic && input.name !== 'dns'
	})
}

function fillNetworkForm(item) {
	const form = document.getElementById('network-form')
	if (!form) return
	form.elements.mode.value = item?.mode === 'static' ? 'static' : 'dhcp'
	form.elements.ip_address.value = item?.ip_address || ''
	form.elements.netmask.value = item?.netmask || ''
	form.elements.gateway.value = item?.gateway || ''
	form.elements.dns.value = item?.dns?.join(', ') || ''
	setTextIfExists('network-link-state', item?.state || '--')
	setTextIfExists('network-current-ip', item?.ip_address || 'None')
	setTextIfExists(
		'network-current-subnet',
		item?.netmask ? `${item.netmask} (/${item.prefix})` : 'None',
	)
	setTextIfExists('network-current-gateway', item?.gateway || 'None')
	setTextIfExists('network-current-mode', item?.mode || 'Unknown')
	setTextIfExists('network-current-dns', item?.dns?.join(', ') || 'None')
	updateNetworkStaticFields()
}

async function loadNetworkSettings() {
	showNetworkMessage('Loading network settings...')
	try {
		const response = await fetch('/api/network')
		handleAuthResponse(response)
		const data = await response.json()
		if (!response.ok) {
			throw new Error(apiErrorMessage(data.detail, 'Could not read network settings'))
		}
		latestNetwork = data
		const select = document.getElementById('network-interface')
		const accessInterfaceAvailable =
			data.access_interface &&
			data.interfaces.some((item) => item.name === data.access_interface)
		select.innerHTML = data.interfaces
			.map(
				(item) =>
					`<option value="${escapeHtml(item.name)}"${accessInterfaceAvailable && item.name !== data.access_interface ? ' disabled' : ''}>${escapeHtml(item.name)} (${escapeHtml(item.mac || 'no MAC')})</option>`,
			)
			.join('')
		select.value = data.selected_interface || data.interfaces[0]?.name || ''
		if (accessInterfaceAvailable) {
			select.value = data.access_interface
		}
		fillNetworkForm(selectedNetworkInterface())
		const mdnsUrl = networkMdnsUrl(data)
		const mdnsLink = document.getElementById('network-mdns-url')
		if (mdnsLink) {
			mdnsLink.textContent = mdnsUrl || '--'
			mdnsLink.href = mdnsUrl || '#'
		}
		document.getElementById('save-network-button').disabled =
			!data.supported || !isAdministrator()
		const originHint = networkOriginUsesHostname()
			? ''
			: ` Open ${mdnsUrl} before changing the network address.`
		showNetworkMessage(
			(data.message || `Host: ${data.hostname}; backend: ${data.backend}.`) + originHint,
		)
	} catch (error) {
		showNetworkMessage(error.message, true)
	}
}

document
	.getElementById('network-interface')
	?.addEventListener('change', () => fillNetworkForm(selectedNetworkInterface()))
document.getElementById('network-mode')?.addEventListener('change', updateNetworkStaticFields)
document.getElementById('refresh-network-button')?.addEventListener('click', loadNetworkSettings)
document.getElementById('network-form')?.addEventListener('submit', async (event) => {
	event.preventDefault()
	if (!isAdministrator()) return
	const form = event.currentTarget
	const payload = Object.fromEntries(new FormData(form).entries())
	const currentInterface = selectedNetworkInterface()
	const addressMayChange =
		payload.mode === 'dhcp' ||
		String(payload.ip_address || '').trim() !==
			String(currentInterface?.ip_address || '').trim()
	if (!networkOriginUsesHostname() && addressMayChange) {
		const mdnsUrl = networkMdnsUrl()
		const message = `For a safe IP change, reopen Amp Panel at ${mdnsUrl || 'its .local address'} and sign in there first.`
		showNetworkMessage(message, true)
		showNotification(message, 'error')
		return
	}
	if (!confirm('Apply the new network settings? The connection may be interrupted.')) return
	let rollbackSeconds = null
	showNetworkMessage('Applying settings...')
	try {
		const response = await fetch('/api/network', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
		})
		handleAuthResponse(response)
		const data = await response.json()
		if (!response.ok) {
			throw new Error(apiErrorMessage(data.detail, 'Could not apply network settings'))
		}
		const confirmation = data.confirmation
		if (!confirmation?.token) {
			throw new Error('The host did not return a network rollback confirmation token.')
		}
		rollbackSeconds = confirmation.expires_in_seconds
		showNetworkMessage(
			`Connection still works. Confirming the change before the ${rollbackSeconds}-second rollback timeout...`,
		)
		const confirmedData = await confirmNetworkChangeWithRetry(
			confirmation.token,
			confirmation.expires_in_seconds,
		)
		latestNetwork = confirmedData
		rollbackSeconds = null
		fillNetworkForm(selectedNetworkInterface())
		showNetworkMessage('Network settings applied and confirmed.')
		showNotification('Network settings applied and confirmed.')
	} catch (error) {
		const message = rollbackSeconds
			? `${error.message || 'Confirmation failed.'} NetworkManager will automatically restore the previous settings within ${rollbackSeconds} seconds.`
			: error instanceof TypeError
				? 'The connection was interrupted. If the host received the change, NetworkManager will automatically restore the previous settings within 60 seconds.'
				: error.message || 'Could not apply network settings.'
		showNetworkMessage(message, true)
		showNotification(message, 'error')
	}
})

function showNtpMessage(message, isError = false) {
	const element = document.getElementById('ntp-message')
	if (!element) return
	element.textContent = message
	element.classList.toggle('error', isError)
}

async function loadNtpStatus(force = false) {
	if (!document.getElementById('ntp-server')) return
	showNtpMessage(force ? 'Querying NTP server...' : 'Loading NTP status...')
	try {
		const response = await fetch(`/api/ntp/status${force ? '?force=true' : ''}`)
		handleAuthResponse(response)
		const data = await response.json()
		if (!response.ok) throw new Error(data.detail || 'Could not read NTP status')

		setTextIfExists('ntp-server', `${data.server}${data.port ? ':' + data.port : ''}`)
		setTextIfExists('ntp-reachable', data.reachable ? 'Yes' : 'No')
		setTextIfExists(
			'ntp-stratum',
			data.stratum !== undefined ? `${data.stratum} (${data.stratum_label || '--'})` : '--',
		)
		setTextIfExists('ntp-reference-id', data.reference_id || '--')
		setTextIfExists('ntp-leap-indicator', data.leap_indicator_label || '--')
		setTextIfExists(
			'ntp-reference-time',
			data.reference_time_utc ? new Date(data.reference_time_utc).toLocaleString() : '--',
		)
		setTextIfExists(
			'ntp-offset',
			data.offset_ms !== undefined && data.offset_ms !== null ? `${data.offset_ms} ms` : '--',
		)
		setTextIfExists(
			'ntp-round-trip',
			data.round_trip_ms !== undefined && data.round_trip_ms !== null
				? `${data.round_trip_ms} ms`
				: '--',
		)
		setTextIfExists(
			'ntp-root-delay',
			data.root_delay_ms !== undefined && data.root_delay_ms !== null
				? `${data.root_delay_ms} ms / ${data.root_dispersion_ms} ms`
				: '--',
		)
		setTextIfExists(
			'ntp-poll-interval',
			data.poll_interval_seconds ? `${data.poll_interval_seconds} s` : '--',
		)
		setTextIfExists(
			'ntp-checked-at',
			data.checked_at ? new Date(data.checked_at).toLocaleString() : '--',
		)

		if (data.reachable) {
			showNtpMessage(`Synchronized with ${data.server}.`)
		} else {
			showNtpMessage(data.error || 'NTP server unreachable.', true)
		}
	} catch (error) {
		showNtpMessage(error.message, true)
	}
}

document.getElementById('refresh-ntp-button')?.addEventListener('click', () => loadNtpStatus(true))

function updateDatabaseStatus(database = {}) {
	setTextIfExists('status-database-records', String(database.records ?? 0))
}

function formatBytes(bytes) {
	const value = Number(bytes)
	if (!Number.isFinite(value)) return '--'
	if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
	if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`
	return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GiB`
}

function formatRetentionDuration(seconds) {
	const value = Number(seconds)
	if (!Number.isFinite(value) || value < 0) return '--'
	if (value < 60) return `${Math.max(0, Math.round(value))} seconds`
	if (value < 3600) return `${(value / 60).toFixed(1)} minutes`
	if (value < 86400) return `${(value / 3600).toFixed(1)} hours`
	if (value < 365 * 86400) return `${(value / 86400).toFixed(1)} days`
	return `${(value / (365 * 86400)).toFixed(1)} years`
}

function formatSampleRate(rate) {
	const value = Number(rate)
	if (!Number.isFinite(value) || value <= 0) return 'Waiting for samples'
	if (value >= 1) return `${value.toFixed(2)} records/s`
	return `1 record / ${(1 / value).toFixed(1)} s`
}

function updateServiceSettingsVisibility() {
	const unlimited = document.getElementById('service-unlimited-history-input')?.checked
	const heartbeatEnabled = document.getElementById('service-heartbeat-enabled-input')?.checked
	const limitField = document.getElementById('service-database-limit-field')
	const heartbeatField = document.getElementById('service-heartbeat-field')
	if (limitField) limitField.hidden = unlimited
	if (heartbeatField) heartbeatField.hidden = !heartbeatEnabled
	updateDatabaseLimitPreview()
}

function updateDatabaseLimitPreview() {
	const preview = document.getElementById('service-database-limit-preview')
	if (!preview) return
	if (document.getElementById('service-unlimited-history-input')?.checked) {
		preview.textContent =
			'No records will be removed automatically. Available disk space is the only limit.'
		return
	}
	const limit = Number(document.getElementById('service-database-limit-input')?.value)
	const rate = Number(latestServiceDatabase.sample_rate_per_second)
	const records = Number(latestServiceDatabase.records || 0)
	if (!Number.isFinite(limit) || limit < 1) {
		preview.textContent = 'Enter a limit between 1 and 10,000,000 records.'
		return
	}
	if (!Number.isFinite(rate) || rate <= 0) {
		preview.textContent = 'The time estimate will appear after at least two samples are stored.'
		return
	}
	const capacity = formatRetentionDuration(limit / rate)
	const remaining = formatRetentionDuration(Math.max(0, limit - records) / rate)
	preview.textContent = `About ${capacity} of history; oldest records start being removed in ${remaining}.`
}

async function loadServiceDiagnostics() {
	if (!isAdministrator() || !document.getElementById('service-database-state')) return
	try {
		const response = await fetch('/api/service-diagnostics')
		handleAuthResponse(response)
		const data = await response.json()
		if (!response.ok) throw new Error(data.detail || 'Could not read service diagnostics')
		const serial = data.serial || {}
		const database = data.database || {}
		const syslog = data.syslog || {}
		latestServiceDatabase = database
		setTextIfExists('service-serial-state', serial.connected ? 'CONNECTED' : 'DISCONNECTED')
		setTextIfExists('service-serial-port', serial.port || '--')
		setTextIfExists('service-serial-baudrate', String(serial.baudrate ?? '--'))
		setTextIfExists('service-serial-error', serial.error || 'None')
		const serialPortInput = document.getElementById('service-serial-port-input')
		if (serialPortInput && !serviceSettingsDirty) {
			const ports = Array.from(
				new Set([serial.port, ...(serial.available_ports || [])].filter(Boolean)),
			)
			serialPortInput.replaceChildren(
				...ports.map((port) => {
					const option = document.createElement('option')
					option.value = port
					option.textContent = port
					return option
				}),
			)
			serialPortInput.value = serial.port || ports[0] || ''
		}
		setTextIfExists('service-database-state', String(database.state || '--').toUpperCase())
		setTextIfExists('service-database-records', String(database.records ?? 0))
		setTextIfExists(
			'service-database-limit',
			database.record_limit === 0 ? 'UNLIMITED' : `${database.record_limit ?? '--'} records`,
		)
		setTextIfExists(
			'service-database-write-rate',
			formatSampleRate(database.sample_rate_per_second),
		)
		setTextIfExists(
			'service-database-retention',
			database.record_limit === 0
				? 'Unlimited (disk space applies)'
				: formatRetentionDuration(database.estimated_retention_seconds),
		)
		setTextIfExists(
			'service-database-time-to-limit',
			database.record_limit === 0
				? 'Not applicable'
				: formatRetentionDuration(database.estimated_seconds_to_limit),
		)
		setTextIfExists(
			'service-database-disk-time',
			formatRetentionDuration(database.estimated_seconds_until_disk_full),
		)
		setTextIfExists('service-database-file', database.file || '--')
		setTextIfExists('service-database-size', formatBytes(database.size_bytes))
		setTextIfExists('service-database-free', formatBytes(database.filesystem_free_bytes))
		setTextIfExists(
			'service-database-discarded',
			String(database.discarded_records_since_start ?? 0),
		)
		setTextIfExists('service-database-error', database.error || 'None')
		setTextIfExists('service-syslog-local', syslog.local_enabled ? 'ENABLED' : 'DISABLED')
		setTextIfExists('service-syslog-destination', syslog.local_destination || '--')
		setTextIfExists('service-syslog-file', syslog.local_file || '--')
		setTextIfExists('service-syslog-remote', syslog.remote_enabled ? 'ENABLED' : 'DISABLED')
		setTextIfExists(
			'service-syslog-host',
			syslog.remote_enabled
				? `${syslog.remote_host}:${syslog.remote_port}`
				: 'Not configured',
		)
		setTextIfExists(
			'service-syslog-protocol',
			syslog.remote_enabled ? String(syslog.remote_protocol).toUpperCase() : '--',
		)
		setTextIfExists(
			'service-syslog-heartbeat',
			syslog.heartbeat_seconds > 0 ? `${syslog.heartbeat_seconds} s` : 'DISABLED',
		)
		const heartbeatInput = document.getElementById('service-heartbeat-input')
		const databaseLimitInput = document.getElementById('service-database-limit-input')
		if (!serviceSettingsDirty) {
			const heartbeatEnabledInput = document.getElementById('service-heartbeat-enabled-input')
			const unlimitedHistoryInput = document.getElementById('service-unlimited-history-input')
			if (heartbeatEnabledInput) heartbeatEnabledInput.checked = syslog.heartbeat_seconds > 0
			if (unlimitedHistoryInput) unlimitedHistoryInput.checked = database.record_limit === 0
			if (heartbeatInput)
				heartbeatInput.value = syslog.heartbeat_seconds > 0 ? syslog.heartbeat_seconds : 300
			if (databaseLimitInput) {
				databaseLimitInput.value =
					database.record_limit > 0 ? database.record_limit : 250000
			}
			updateServiceSettingsVisibility()
		} else {
			updateDatabaseLimitPreview()
		}
	} catch (error) {
		setTextIfExists('service-database-state', 'API ERROR')
		console.error('Error loading service diagnostics:', error)
	}
}

document
	.getElementById('refresh-services-button')
	?.addEventListener('click', loadServiceDiagnostics)

const serviceSettingsForm = document.getElementById('service-settings-form')
serviceSettingsForm?.addEventListener('input', () => {
	serviceSettingsDirty = true
	updateServiceSettingsVisibility()
})

serviceSettingsForm?.addEventListener('submit', async (event) => {
	event.preventDefault()
	try {
		const heartbeatEnabled = document.getElementById('service-heartbeat-enabled-input').checked
		const unlimitedHistory = document.getElementById('service-unlimited-history-input').checked
		const response = await fetch('/api/service-diagnostics/settings', {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				serial_port: document.getElementById('service-serial-port-input').value,
				syslog_heartbeat_seconds: heartbeatEnabled
					? Number(document.getElementById('service-heartbeat-input').value)
					: 0,
				database_max_records: unlimitedHistory
					? 0
					: Number(document.getElementById('service-database-limit-input').value),
			}),
		})
		handleAuthResponse(response)
		const result = await response.json()
		if (!response.ok) throw new Error(result.detail || 'Could not save service settings')
		const suffix = result.pruned_records
			? ` ${result.pruned_records} oldest database records were removed.`
			: ''
		showNotification(`Service settings saved.${suffix}`)
		serviceSettingsDirty = false
		await loadServiceDiagnostics()
	} catch (error) {
		showNotification(error.message || 'Could not save service settings.', 'error')
	}
})
