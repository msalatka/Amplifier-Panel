// Administrator editor for the complete XML-to-dashboard mapping.
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
		message.textContent = ''
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
		message.textContent = 'Mapping saved. Device views will use it on the next XML poll.'
		showNotification('Variable blocks saved.')
	} catch (error) {
		message.classList.add('error')
		message.textContent = error.message
	} finally {
		saveButton.disabled = false
	}
})
