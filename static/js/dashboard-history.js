// Shared chart rendering retained from the original dashboard.
function formatTimeAxisTick(value, timeBounds) {
	const date = new Date(Number(value))
	if (Number.isNaN(date.getTime())) return ''
	const pad = (number) => String(number).padStart(2, '0')
	const spanMs = Math.max(0, Number(timeBounds.max) - Number(timeBounds.min))
	const dayMs = 24 * 60 * 60 * 1000
	if (spanMs > 365 * dayMs) {
		return `${pad(date.getMonth() + 1)}.${date.getFullYear()}`
	}
	if (spanMs > dayMs) {
		return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`
	}
	const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`
	return spanMs <= 5 * 60 * 1000 ? `${time}:${pad(date.getSeconds())}` : time
}

function getTimeAxisTitle(timeBounds) {
	const spanMs = Math.max(0, Number(timeBounds.max) - Number(timeBounds.min))
	const dayMs = 24 * 60 * 60 * 1000
	if (spanMs > 365 * dayMs) return 'Month (MM.YYYY)'
	if (spanMs > dayMs) return 'Date (DD.MM)'
	return 'Time (24-hour)'
}

function createOrUpdateChart(
	existingChart,
	canvasId,
	points,
	fullTimestamps,
	datasets,
	yLabel,
	timeBounds,
) {
	const canvas = document.getElementById(canvasId)
	if (!canvas || typeof Chart === 'undefined') return existingChart
	const timeValues = points.map((point) => new Date(point.time).getTime())
	datasets.forEach((dataset) => {
		dataset.data = dataset.data.map((value, index) => ({
			x: timeValues[index],
			y: value,
		}))
		const savedVisibility = chartSeriesVisibility.get(`${canvasId}:${dataset.label}`)
		if (savedVisibility !== undefined) dataset.hidden = !savedVisibility
	})

	if (existingChart === null) {
		const chart = new Chart(canvas, {
			type: 'line',
			data: { datasets: datasets },
			options: {
				animation: false,
				responsive: true,
				maintainAspectRatio: false,
				locale: 'pl-PL',
				plugins: {
					tooltip: {
						callbacks: {
							title: (items) => {
								if (!items.length) return ''
								return (
									items[0].chart.fullTimestamps?.[items[0].dataIndex] ||
									items[0].label
								)
							},
						},
					},
					legend: {
						labels: { usePointStyle: true },
						onClick: (_event, legendItem, legend) => {
							const index = legendItem.datasetIndex
							const dataset = legend.chart.data.datasets[index]
							const visible = !legend.chart.isDatasetVisible(index)
							chartSeriesVisibility.set(`${canvasId}:${dataset.label}`, visible)
							legend.chart.setDatasetVisibility(index, visible)
							legend.chart.update()
						},
						onHover: (event) => {
							if (event.native && event.native.target)
								event.native.target.style.cursor = 'pointer'
						},
						onLeave: (event) => {
							if (event.native && event.native.target)
								event.native.target.style.cursor = 'default'
						},
					},
				},
				scales: {
					x: {
						type: 'linear',
						min: timeBounds.min,
						max: timeBounds.max,
						title: {
							display: true,
							text: getTimeAxisTitle(timeBounds),
						},
						afterBuildTicks: (scale) => {
							const interval = (scale.max - scale.min) / 4
							scale.ticks = Array.from({ length: 5 }, (_item, index) => ({
								value: scale.min + interval * index,
							}))
						},
						ticks: {
							autoSkip: false,
							maxRotation: 0,
							minRotation: 0,
							callback: (value) => formatTimeAxisTick(value, timeBounds),
						},
					},
					y: { title: { display: true, text: yLabel } },
				},
			},
		})
		chart.fullTimestamps = fullTimestamps
		return chart
	}

	existingChart.data.datasets = datasets
	existingChart.options.scales.x.min = timeBounds.min
	existingChart.options.scales.x.max = timeBounds.max
	existingChart.options.scales.x.title.text = getTimeAxisTitle(timeBounds)
	existingChart.options.scales.x.ticks.callback = (value) => formatTimeAxisTick(value, timeBounds)
	datasets.forEach((dataset, index) => {
		const savedVisibility = chartSeriesVisibility.get(`${canvasId}:${dataset.label}`)
		if (savedVisibility !== undefined)
			existingChart.setDatasetVisibility(index, savedVisibility)
	})
	existingChart.fullTimestamps = fullTimestamps
	existingChart.update()
	return existingChart
}

function resizeChartInCard(card) {
	const canvas = card.querySelector('canvas')
	if (!canvas || typeof Chart === 'undefined' || typeof Chart.getChart !== 'function') return
	const chart = Chart.getChart(canvas)
	if (chart) {
		window.requestAnimationFrame(() => {
			window.requestAnimationFrame(() => chart.resize())
		})
		window.setTimeout(() => chart.resize(), 200)
	}
}

function getChartSizeIcon(expanded) {
	const path = expanded
		? 'M8 3v5H3 M16 3v5h5 M8 21v-5H3 M16 21v-5h5'
		: 'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5'
	return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"></path></svg>`
}

function setChartExpanded(card, expanded) {
	card.classList.toggle('expanded', expanded)
	const button = card.querySelector('.chart-expand-button')
	const title = card.querySelector('h3')?.textContent || 'chart'
	if (button) {
		button.innerHTML = getChartSizeIcon(expanded)
		button.setAttribute('aria-label', `${expanded ? 'Reduce' : 'Expand'} ${title} chart`)
		button.title = expanded ? 'Reduce chart' : 'Expand chart'
		button.setAttribute('aria-pressed', String(expanded))
	}
	resizeChartInCard(card)
}

function setupChartExpansion() {
	document.querySelectorAll('.chart-expand-button').forEach((button) => {
		button.innerHTML = getChartSizeIcon(false)
		button.setAttribute('aria-pressed', 'false')
		button.addEventListener('click', () => {
			const card = button.closest('.chart-card')
			if (!card) return
			const shouldExpand = !card.classList.contains('expanded')
			setChartExpanded(card, shouldExpand)
		})
	})

	document.addEventListener('keydown', (event) => {
		if (event.key !== 'Escape') return
		document
			.querySelectorAll('.chart-card.expanded')
			.forEach((card) => setChartExpanded(card, false))
	})
}
const SNMP_FIELD_LABELS = {
	status: 'Connection status',
	PiA: 'PiA',
	PoA: 'PoA',
	PiB: 'PiB',
	PoB: 'PoB',
	gain_set: 'Gain set',
	gain_actual: 'Actual gain',
	gain_delta: 'Gain delta',
	temperature: 'Temperature',
	seq_nr: 'Sequence nr',
}
