// Administrator editor for the complete XML-to-dashboard mapping.
let xmlMappingSavedContent = null
let pendingXmlVariable = null

function locateXmlVariable(variable) {
	const content = document.getElementById('xml-mapping-content')
	let mapping
	try {
		mapping = JSON.parse(content.value)
	} catch {
		showNotification(
			'The mapping JSON must be valid before a variable can be located.',
			'error',
		)
		return
	}
	const field = mapping[selectedDeviceId]?.sections
		?.find((section) => section.key === variable.section)
		?.fields?.find((item) => item.key === variable.key)
	if (!field) {
		showNotification(
			'This automatically discovered variable is not defined in the mapping JSON.',
			'error',
		)
		return
	}

	const deviceMarker = `${JSON.stringify(selectedDeviceId)}: {`
	const sectionMarker = `"key": ${JSON.stringify(variable.section)}`
	const fieldMarker = `"key": ${JSON.stringify(variable.key)}`
	const deviceStart = content.value.indexOf(deviceMarker)
	const sectionStart = content.value.indexOf(sectionMarker, deviceStart)
	const fieldsStart = content.value.indexOf('"fields": [', sectionStart)
	const fieldStart = content.value.indexOf(fieldMarker, fieldsStart)
	if (fieldStart < 0) {
		showNotification('Could not locate this variable in the mapping text.', 'error')
		return
	}
	content.focus()
	content.setSelectionRange(fieldStart, fieldStart + fieldMarker.length)
	const line = content.value.slice(0, fieldStart).split('\n').length - 1
	const lineHeight = Number.parseFloat(getComputedStyle(content).lineHeight) || 20
	content.scrollTop = Math.max(0, line * lineHeight - content.clientHeight / 3)
	showNotification(`Editing ${field.label || variable.key}.`)
}

window.openXmlVariableEditor = (variable) => {
	if (variable.automatic) {
		showNotification(
			'This automatically discovered variable is not defined in the mapping JSON.',
			'error',
		)
		return
	}
	pendingXmlVariable = variable
	if (!setActiveTab('variable-blocks')) return
	history.pushState(
		null,
		'',
		`${window.location.pathname}${window.location.search}#variable-blocks`,
	)
}

function hasUnsavedXmlMappingChanges() {
	const content = document.getElementById('xml-mapping-content')
	return xmlMappingSavedContent !== null && content?.value !== xmlMappingSavedContent
}

window.confirmDiscardXmlMappingChanges = () => {
	if (!hasUnsavedXmlMappingChanges()) return true
	const shouldContinue = window.confirm(
		'You have unsaved JSON changes. Continue without saving them?',
	)
	if (shouldContinue) xmlMappingSavedContent = null
	return shouldContinue
}

window.addEventListener('beforeunload', (event) => {
	if (!hasUnsavedXmlMappingChanges()) return
	event.preventDefault()
	event.returnValue = ''
})

async function loadXmlMapping() {
	if (!isAdministrator()) return
	const content = document.getElementById('xml-mapping-content')
	const message = document.getElementById('xml-mapping-message')
	if (!content || !message) return

	message.classList.remove('error')
	message.textContent = 'Loading mapping...'
	try {
		const response = await fetch('/api/xml-mapping')
		handleAuthResponse(response)
		if (!response.ok) throw await responseError(response, 'Could not load XML mapping')
		const result = await response.json()
		setTextIfExists('xml-mapping-path', result.path)
		content.value = result.content
		xmlMappingSavedContent = result.content
		message.textContent = ''
		if (pendingXmlVariable) {
			const variable = pendingXmlVariable
			pendingXmlVariable = null
			locateXmlVariable(variable)
		}
	} catch (error) {
		message.classList.add('error')
		message.textContent = error.message
	}
}

document.getElementById('xml-mapping-form')?.addEventListener('submit', async (event) => {
	event.preventDefault()
	if (!isAdministrator()) return
	const content = document.getElementById('xml-mapping-content')
	const message = document.getElementById('xml-mapping-message')
	const saveButton = event.currentTarget.querySelector('button[type="submit"]')
	message.classList.remove('error')
	message.textContent = 'Validating and saving...'
	saveButton.disabled = true
	try {
		const response = await fetch('/api/xml-mapping', {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ content: content.value }),
		})
		handleAuthResponse(response)
		if (!response.ok) throw await responseError(response, 'Could not save XML mapping')
		const result = await response.json()
		setTextIfExists('xml-mapping-path', result.path)
		content.value = result.content
		xmlMappingSavedContent = result.content
		message.textContent = 'Mapping saved. Device views will use it on the next XML poll.'
		showNotification('Variables saved.')
	} catch (error) {
		message.classList.add('error')
		message.textContent = error.message
	} finally {
		saveButton.disabled = false
	}
})
