// Authentication, settings, warnings, access control and SNMP UI.
async function loadSettings() {
	if (!currentUser) return

	const response = await fetch('/api/settings')
	handleAuthResponse(response)
	dashboardSettings = await response.json()
	updateSettingsForm()
}

async function checkAuth() {
	const response = await fetch('/api/auth/me')

	if (!response.ok) {
		currentUser = null
		showLogin()
		return false
	}

	const json = await response.json()
	currentUser = json.user
	applyRoleUi()
	showApp()
	restoreTabFromUrl()
	return true
}

async function login(username, password) {
	const response = await fetch('/api/auth/login', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ username, password }),
	})

	if (!response.ok) {
		let message = 'Invalid username or password'
		try {
			const body = await response.json()
			if (body.detail) message = body.detail
		} catch {
			// A non-JSON error response keeps the generic authentication message.
		}
		throw new Error(message)
	}

	const json = await response.json()
	currentUser = json.user
	applyRoleUi()
	showApp()
	restoreTabFromUrl()
}

async function logout() {
	await fetch('/api/auth/logout', { method: 'POST' })
	currentUser = null
	showLogin()
}

function updateSettingsForm() {
	if (!dashboardSettings) return

	const gainToleranceInput = document.getElementById('gain-tolerance-input')

	if (gainToleranceInput) {
		gainToleranceInput.value = dashboardSettings.gain_tolerance
	}
	const gainSetInput = document.getElementById('gain-set-input')
	if (gainSetInput && dashboardSettings.gain_set_limits) {
		gainSetInput.min = dashboardSettings.gain_set_limits.min
		gainSetInput.max = dashboardSettings.gain_set_limits.max
	}

	Object.entries(dashboardSettings.warn_limits || {}).forEach(([field, limits]) => {
		setInputValue(`[data-limit-field="${field}"][data-limit-side="min"]`, limits.min)
		setInputValue(`[data-limit-field="${field}"][data-limit-side="max"]`, limits.max)
	})
}

async function saveSettings() {
	if (!canOperate()) return

	const warnLimits = {}
	const gainInput = document.getElementById('gain-set-input')
	const gainValueText = gainInput.value.trim()

	document.querySelectorAll('[data-limit-field]').forEach((input) => {
		const field = input.dataset.limitField
		const side = input.dataset.limitSide

		if (!warnLimits[field]) {
			warnLimits[field] = { min: null, max: null }
		}

		warnLimits[field][side] = valueOrNull(input)
	})
	Object.entries(warnLimits).forEach(([field, limits]) => {
		if (limits.min !== null && limits.max !== null && limits.min >= limits.max) {
			throw new Error(`${field}: MIN threshold must be lower than MAX threshold.`)
		}
	})

	const response = await fetch('/api/settings', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			gain_tolerance: Number(document.getElementById('gain-tolerance-input').value || 0),
			warn_limits: warnLimits,
		}),
	})
	handleAuthResponse(response)
	if (!response.ok) throw await responseError(response, 'Could not save thresholds')

	dashboardSettings = await response.json()
	updateSettingsForm()

	let gainError = null
	if (gainInputEdited && gainValueText !== '') {
		const gainResponse = await fetch('/api/set_gain', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ gain_set: Number(gainValueText) }),
		})
		handleAuthResponse(gainResponse)

		if (gainResponse.ok) {
			gainInputEdited = false
			gainInput.value = Number(gainValueText)
		} else {
			gainError = await responseError(
				gainResponse,
				'Could not send gain setpoint to the device',
			)
		}
	}

	return { gainError }
}

async function updateDashboard() {
	if (!currentUser) return

	try {
		const response = await fetch('/api/latest')
		handleAuthResponse(response)
		if (!response.ok) throw new Error('HTTP error ' + response.status)
		const json = await response.json()
		const data = json.data || {}
		if (deviceProfile === 'fts-ls') renderFtsStatus(json.fts_ls || data.fts_ls)

		setTextIfExists('PiA', formatDbm(data.PiA))
		setTextIfExists('PoA', formatDbm(data.PoA))
		setTextIfExists('PiB', formatDbm(data.PiB))
		setTextIfExists('PoB', formatDbm(data.PoB))
		setTextIfExists('gain-actual', formatDb(data.gain_actual))
		setTextIfExists('gain-delta', formatDb(data.gain_delta))
		setTextIfExists('temperature', formatTemperature(data.temperature))

		const gainSetValue = data.gain_set !== undefined ? data.gain_set : json.last_known_gain_set
		const gainInput = document.getElementById('gain-set-input')
		if (gainInput && document.activeElement !== gainInput && !gainInputEdited) {
			gainInput.value =
				gainSetValue === null || gainSetValue === undefined
					? ''
					: Number(gainSetValue).toFixed(2)
		}

		setTextIfExists('status-last-update', formatTime(json.last_update))
		setTextIfExists('status-system-time', formatTime(json.system_time))
		updateDatabaseStatus(json.database)

		const statusEl = document.getElementById('status-connection')

		if (json.connected) {
			statusEl.textContent = 'CONNECTED'
			statusEl.className = 'status-ok'
		} else {
			statusEl.textContent = 'DISCONNECTED'
			statusEl.className = 'status-error'
		}
	} catch (error) {
		const statusEl = document.getElementById('status-connection')
		statusEl.textContent = 'API ERROR'
		statusEl.className = 'status-error'
		updateDatabaseStatus({ state: 'error', ready: false, records: '--' })
		console.error('Error fetching /api/latest:', error)
	}
}

function renderActiveWarnings(warnings) {
	const body = document.getElementById('active-warnings-table-body')
	setTextIfExists('warning-count', String(warnings.length))
	if (!body) return
	if (!warnings.length) {
		body.innerHTML = '<tr><td colspan="7">No active warnings</td></tr>'
		return
	}
	body.innerHTML = warnings
		.map((warning) => {
			const stateLabel = warning.acknowledged ? 'Acknowledged' : 'Open'
			const stateClass = warning.acknowledged
				? 'warning-state-acknowledged'
				: 'warning-state-open'
			return `
				<tr>
					<td>${escapeHtml(formatDateTime(warning.opened_at || warning.event_time))}</td>
					<td>${escapeHtml(warning.label || warning.field || '--')}</td>
					<td>${escapeHtml(formatPlainNumber(warning.value))}</td>
					<td>${escapeHtml(formatPlainNumber(warning.target))}</td>
					<td>${escapeHtml(formatPlainNumber(warning.delta))}</td>
					<td><span class="warning-state ${stateClass}">${stateLabel}</span></td>
					<td>${escapeHtml(warning.message || '--')}</td>
				</tr>
			`
		})
		.join('')
}

function warningHistoryQuery() {
	const range = document.getElementById('warning-range-filter')?.value || '24h'
	const field = document.getElementById('warning-field-filter')?.value || ''
	const status = document.getElementById('warning-status-filter')?.value || ''
	const params = new URLSearchParams({
		range,
		limit: String(warningHistoryLimit),
		offset: String(warningHistoryOffset),
	})
	if (field) params.set('field', field)
	if (status) params.set('status', status)
	if (range === 'custom') {
		if (warningHistoryStart) params.set('start', warningHistoryStart)
		if (warningHistoryEnd) params.set('end', warningHistoryEnd)
	}
	return params.toString()
}

function renderWarningHistory(data) {
	const events = data.history || []
	const body = document.getElementById('warning-history-table-body')
	warningHistoryTotal = Number(data.total || 0)
	if (body) {
		if (!data.source_available) {
			body.innerHTML = '<tr><td colspan="8">System warning log is not available</td></tr>'
		} else {
			const unreadableRow =
				Number(data.unreadable_files || 0) > 0
					? '<tr><td colspan="8">One or more rotated warning logs could not be read</td></tr>'
					: ''
			const eventRows = events
				.map((event) => {
					const eventName = String(event.event || '').toUpperCase()
					const isCleared = eventName === 'CLEARED'
					return `
						<tr>
							<td>${escapeHtml(formatDateTime(event.event_time))}</td>
							<td><span class="warning-state ${isCleared ? 'warning-state-cleared' : 'warning-state-open'}">${isCleared ? 'Cleared' : 'Opened'}</span></td>
							<td>${escapeHtml(event.label || event.field || '--')}</td>
							<td>${escapeHtml(formatPlainNumber(event.value))}</td>
							<td>${escapeHtml(formatPlainNumber(event.target))}</td>
							<td>${escapeHtml(formatPlainNumber(event.delta))}</td>
							<td>${escapeHtml(formatDuration(event.duration_seconds))}</td>
							<td>${escapeHtml(event.message || '--')}</td>
						</tr>
					`
				})
				.join('')
			body.innerHTML =
				unreadableRow +
				(eventRows || '<tr><td colspan="8">No warning events in this range</td></tr>')
		}
	}

	const first = warningHistoryTotal ? warningHistoryOffset + 1 : 0
	const last = Math.min(warningHistoryOffset + events.length, warningHistoryTotal)
	setTextIfExists('warning-history-count', `${warningHistoryTotal} events`)
	setTextIfExists('warning-page-status', `${first}–${last} of ${warningHistoryTotal}`)
	const previous = document.getElementById('warning-previous-page')
	const next = document.getElementById('warning-next-page')
	if (previous) previous.disabled = warningHistoryOffset === 0
	if (next) next.disabled = warningHistoryOffset + events.length >= warningHistoryTotal
}

async function updateWarningsTable(forceHistory = false) {
	if (!currentUser) return

	try {
		const response = await fetch('/api/warnings/active')
		handleAuthResponse(response)
		if (!response.ok) throw await responseError(response, 'Could not read active warnings')
		const json = await response.json()
		renderActiveWarnings(json.active || [])

		const warningsPanel = document.querySelector('.tab-panel[data-tab="warnings"]')
		const now = Date.now()
		if (
			!warningsPanel?.classList.contains('active') ||
			(!forceHistory && now - lastWarningHistoryRefresh < 15000)
		) {
			return
		}
		const historyResponse = await fetch(`/api/warnings?${warningHistoryQuery()}`)
		handleAuthResponse(historyResponse)
		if (!historyResponse.ok) {
			throw await responseError(historyResponse, 'Could not read warning history')
		}
		renderWarningHistory(await historyResponse.json())
		lastWarningHistoryRefresh = now
	} catch (error) {
		console.error('Error fetching warnings:', error)
		lastWarningHistoryRefresh = Date.now()
		const warningsPanel = document.querySelector('.tab-panel[data-tab="warnings"]')
		if (warningsPanel?.classList.contains('active')) {
			showNotification(error.message || 'Could not read warnings.', 'error')
		}
	}
}

function setupSettingsButtons() {
	const saveButton = document.getElementById('save-settings-button')
	const acknowledgeButton = document.getElementById('acknowledge-warnings-button')

	if (saveButton) {
		saveButton.addEventListener('click', async () => {
			try {
				const result = await saveSettings()
				if (result.gainError) {
					showNotification(
						`Thresholds saved, but gain setpoint was not sent: ${result.gainError.message}`,
						'warning',
					)
				} else {
					showNotification('Setpoints and thresholds saved.')
				}
			} catch (error) {
				showNotification(
					error.message || 'Could not save setpoints and thresholds.',
					'error',
				)
				console.error('Error saving setpoints and thresholds:', error)
			}
		})
	}

	if (acknowledgeButton) {
		acknowledgeButton.addEventListener('click', async () => {
			if (!canOperate()) return
			try {
				const response = await fetch('/api/warnings/acknowledge', { method: 'POST' })
				handleAuthResponse(response)
				if (!response.ok)
					throw await responseError(response, 'Could not acknowledge warnings')
				const data = await response.json()
				await updateWarningsTable(true)
				showNotification(`${data.acknowledged || 0} active warnings acknowledged.`)
			} catch (error) {
				showNotification(error.message, 'error')
			}
		})
	}
}

function setupWarningFilters() {
	const rangeFilter = document.getElementById('warning-range-filter')
	const customRange = document.getElementById('warning-custom-range')
	const refresh = () => {
		if (rangeFilter?.value === 'custom' && !warningHistoryStart) {
			showNotification('Select and apply a custom start time.', 'error')
			return
		}
		warningHistoryOffset = 0
		lastWarningHistoryRefresh = 0
		updateWarningsTable(true)
	}

	rangeFilter?.addEventListener('change', () => {
		if (customRange) customRange.hidden = rangeFilter.value !== 'custom'
		if (rangeFilter.value !== 'custom') refresh()
	})
	document.getElementById('warning-field-filter')?.addEventListener('change', refresh)
	document.getElementById('warning-status-filter')?.addEventListener('change', refresh)
	document.getElementById('refresh-warning-history-button')?.addEventListener('click', refresh)
	document.getElementById('apply-warning-range-button')?.addEventListener('click', () => {
		warningHistoryStart = localDateTimeToIso(
			document.getElementById('warning-start-input')?.value,
		)
		warningHistoryEnd = localDateTimeToIso(document.getElementById('warning-end-input')?.value)
		if (!warningHistoryStart) {
			showNotification('Select a valid start time.', 'error')
			return
		}
		refresh()
	})
	document.getElementById('warning-previous-page')?.addEventListener('click', () => {
		warningHistoryOffset = Math.max(0, warningHistoryOffset - warningHistoryLimit)
		lastWarningHistoryRefresh = 0
		updateWarningsTable(true)
	})
	document.getElementById('warning-next-page')?.addEventListener('click', () => {
		if (warningHistoryOffset + warningHistoryLimit >= warningHistoryTotal) return
		warningHistoryOffset += warningHistoryLimit
		lastWarningHistoryRefresh = 0
		updateWarningsTable(true)
	})
}

async function loadAccessUsers() {
	if (!isAdministrator()) return

	try {
		const response = await fetch('/api/access/users')
		handleAuthResponse(response)
		if (!response.ok) throw new Error('HTTP error ' + response.status)
		const json = await response.json()
		const users = json.users || []
		const localAuthentication = json.authentication_mode === 'local'
		const passwordField = document.getElementById('access-password-field')
		const passwordInput = document.getElementById('access-password-input')
		passwordField.hidden = !localAuthentication
		passwordInput.required = localAuthentication
		document.querySelectorAll('.access-password-column').forEach((element) => {
			element.hidden = !localAuthentication
		})
		const body = document.getElementById('access-users-table-body')

		setTextIfExists('access-users-count', `${users.length} users`)

		if (!body) return

		if (!users.length) {
			body.innerHTML = `<tr><td colspan="${localAuthentication ? 5 : 4}">No users</td></tr>`
			return
		}

		body.innerHTML = users
			.map((user) => {
				const username = escapeHtml(user.username)
				const role = escapeHtml(user.role)
				const activeChecked = user.active ? 'checked' : ''
				const passwordCell = localAuthentication
					? `<td><input data-access-password type="password" autocomplete="new-password" placeholder="Leave blank to keep"></td>`
					: ''

				return `
                <tr data-username="${username}">
                    <td>${username}</td>
                    <td>
                        <select data-access-role>
                            <option value="Administrator" ${role === 'Administrator' ? 'selected' : ''}>Administrator</option>
                            <option value="Operator" ${role === 'Operator' ? 'selected' : ''}>Operator</option>
                            <option value="Viewer" ${role === 'Viewer' ? 'selected' : ''}>Viewer</option>
                        </select>
                    </td>
                    <td>
                        <label class="table-toggle">
                            <input data-access-active type="checkbox" ${activeChecked}>
                            Active
                        </label>
                    </td>
                    ${passwordCell}
                    <td>
                        <div class="action-buttons">
                            <button data-access-save type="button">Save</button>
                            <button data-access-delete type="button">Delete</button>
                        </div>
                    </td>
                </tr>
            `
			})
			.join('')
	} catch (error) {
		console.error('Error loading access users:', error)
	}
}

function getAccessRowPayload(row) {
	const payload = {
		role: row.querySelector('[data-access-role]').value,
		active: row.querySelector('[data-access-active]').checked,
	}
	const password = row.querySelector('[data-access-password]')?.value
	if (password) payload.password = password
	return payload
}

function setupAccessControl() {
	const form = document.getElementById('access-user-form')
	const tableBody = document.getElementById('access-users-table-body')

	if (form) {
		form.addEventListener('submit', async (event) => {
			event.preventDefault()
			if (!isAdministrator()) return

			const usernameInput = document.getElementById('access-username-input')
			const roleInput = document.getElementById('access-role-input')
			const activeInput = document.getElementById('access-active-input')
			const passwordInput = document.getElementById('access-password-input')

			const username = usernameInput.value.trim()
			const password = passwordInput?.value
			try {
				const response = await fetch('/api/access/users', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({
						username,
						role: roleInput.value,
						active: activeInput.checked,
						...(password ? { password } : {}),
					}),
				})
				handleAuthResponse(response)

				if (!response.ok) throw await responseError(response, 'Could not add user')

				form.reset()
				activeInput.checked = true
				await loadAccessUsers()
				showNotification(`User "${username}" added.`)
			} catch (error) {
				showNotification(error.message, 'error')
				console.error('Error adding access user:', error)
			}
		})
	}

	if (tableBody) {
		tableBody.addEventListener('click', async (event) => {
			const row = event.target.closest('tr[data-username]')
			if (!row) return

			const username = row.dataset.username

			if (event.target.matches('[data-access-save]')) {
				if (!isAdministrator()) return

				try {
					const response = await fetch(
						`/api/access/users/${encodeURIComponent(username)}`,
						{
							method: 'PUT',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify(getAccessRowPayload(row)),
						},
					)
					handleAuthResponse(response)

					if (!response.ok) throw await responseError(response, 'Could not update user')

					await loadAccessUsers()
					showNotification(`User "${username}" saved.`)
				} catch (error) {
					showNotification(error.message, 'error')
					console.error('Error saving access user:', error)
				}
			}

			if (event.target.matches('[data-access-delete]')) {
				if (!isAdministrator()) return

				if (!confirm(`Delete user "${username}"?`)) return

				try {
					const response = await fetch(
						`/api/access/users/${encodeURIComponent(username)}`,
						{
							method: 'DELETE',
						},
					)
					handleAuthResponse(response)

					if (!response.ok) throw await responseError(response, 'Could not delete user')

					await loadAccessUsers()
					showNotification(`User "${username}" deleted.`)
				} catch (error) {
					showNotification(error.message, 'error')
					console.error('Error deleting access user:', error)
				}
			}
		})
	}
}

async function updateSnmpLiveValues() {
	const container = document.getElementById('snmp-live-values')
	if (!container || !currentUser) return
	try {
		const response = await fetch('/api/snmp/live_data')
		handleAuthResponse(response)
		if (!response.ok) throw new Error(`HTTP ${response.status}`)
		const data = await response.json()
		const rows = Object.entries(SNMP_FIELD_LABELS)
			.filter(([field]) => field in data)
			.map(
				([field, label]) =>
					`<div class="limit-row"><span>${escapeHtml(label)}</span><span>${escapeHtml(data[field])}</span></div>`,
			)
			.join('')
		container.innerHTML = rows
			? `<div class="limit-list">${rows}</div>`
			: '<p>Waiting for SNMP data...</p>'
	} catch (error) {
		container.innerHTML = '<p>Could not load SNMP data.</p>'
		console.error(error)
	}
}

async function loadSnmpSettings() {
	if (!currentUser) return
	const response = await fetch('/api/snmp/settings')
	handleAuthResponse(response)
	if (!response.ok) return
	const settings = await response.json()
	document.getElementById('snmp-enabled-input').checked = settings.enabled
	document.getElementById('snmp-port-input').value = settings.port
	document.getElementById('snmp-community-input').value = settings.community
	document.getElementById('snmp-trap-host-input').value = settings.trap_host
	document.getElementById('snmp-trap-port-input').value = settings.trap_port
	updateSnmpLiveValues()
}

const snmpForm = document.getElementById('snmp-settings-form')
if (snmpForm) {
	snmpForm.addEventListener('submit', async (event) => {
		event.preventDefault()
		const payload = {
			enabled: document.getElementById('snmp-enabled-input').checked,
			port: Number(document.getElementById('snmp-port-input').value),
			community: document.getElementById('snmp-community-input').value.trim(),
			trap_host: document.getElementById('snmp-trap-host-input').value.trim(),
			trap_port: Number(document.getElementById('snmp-trap-port-input').value),
		}
		try {
			const response = await fetch('/api/snmp/settings', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload),
			})
			handleAuthResponse(response)
			if (!response.ok) throw await responseError(response, 'Could not save SNMP settings')
			await loadSnmpSettings()
			showNotification('SNMP settings saved.')
		} catch (error) {
			showNotification(error.message, 'error')
			console.error('Error saving SNMP settings:', error)
		}
	})
}
const gainSetInput = document.getElementById('gain-set-input')
if (gainSetInput) {
	gainSetInput.addEventListener('input', () => {
		gainInputEdited = true
	})
}

function setupAuth() {
	const loginForm = document.getElementById('login-form')
	const logoutButton = document.getElementById('logout-button')
	const syslogButton = document.getElementById('download-syslog-button')

	if (loginForm) {
		loginForm.addEventListener('submit', async (event) => {
			event.preventDefault()

			const error = document.getElementById('login-error')
			const username = document.getElementById('login-username').value.trim()
			const password = document.getElementById('login-password').value

			error.textContent = ''

			try {
				await login(username, password)
				loginForm.reset()
				await startDataRefresh()
			} catch (loginError) {
				error.textContent = loginError.message || 'Invalid username or password'
			}
		})
	}

	if (logoutButton) {
		logoutButton.addEventListener('click', async () => {
			await logout()
		})
	}

	if (syslogButton) {
		syslogButton.addEventListener('click', () => {
			if (!isAdministrator()) return
			window.location.href = '/api/syslog/export.log'
		})
	}
}

let ftsFormTarget = null
const ftsDirtyInputs = new Set()
const ftsInputBaselines = new Map()
