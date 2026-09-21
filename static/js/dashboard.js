// Authentication, access administration and existing SNMP interface.
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
