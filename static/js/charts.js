// NickUtc - Chart.js Chart Definitions

const chartDefaults = {
    color: '#888',
    borderColor: '#2a2a40',
};

Chart.defaults.color = '#888';
Chart.defaults.borderColor = '#2a2a40';

let timelineChartInstance = null;
let tzHistoryChartInstance = null;
let wakeupChartInstance = null;

// --- Chart 1: Activity Timeline (Floating Bars) ---

function renderTimelineChart(onlinePeriods) {
    const ctx = document.getElementById('timelineChart').getContext('2d');

    if (timelineChartInstance) {
        timelineChartInstance.destroy();
    }

    // Filter to last 48 hours (use UTC)
    const nowUtc = Date.now();
    const cutoff = new Date(nowUtc - 48 * 60 * 60 * 1000);

    // Split periods by source
    const sourceConfig = {
        telegram: { label: 'Telegram', color: '#2aabee', row: 'TG' },
        discord:  { label: 'Discord',  color: '#5865f2', row: 'DC' },
    };

    // Check which sources are present
    const sources = [...new Set(onlinePeriods.map(p => p.source || 'telegram'))];
    const hasBothSources = sources.length > 1;

    const datasets = [];
    for (const source of sources) {
        const cfg = sourceConfig[source] || { label: source, color: '#4ade80', row: source };
        const bars = onlinePeriods
            .filter(p => (p.source || 'telegram') === source)
            .map(p => ({
                x: [new Date(p.start).getTime(), new Date(p.end).getTime()],
                y: hasBothSources ? cfg.row : 'Activity',
            }))
            .filter(b => b.x[1] > cutoff.getTime());

        bars.forEach(b => {
            if (b.x[0] < cutoff.getTime()) b.x[0] = cutoff.getTime();
        });

        datasets.push({
            label: cfg.label,
            data: bars,
            backgroundColor: cfg.color,
            borderRadius: 2,
            borderSkipped: false,
            barPercentage: 0.6,
        });
    }

    timelineChartInstance = new Chart(ctx, {
        type: 'bar',
        data: { datasets },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: hasBothSources, labels: { boxWidth: 12 } },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const [start, end] = ctx.raw.x;
                            const d1 = new Date(start);
                            const d2 = new Date(end);
                            const durMin = Math.round((end - start) / 60000);
                            const startStr = d1.toLocaleTimeString('en-GB', { hour12: false });
                            const endStr = d2.toLocaleTimeString('en-GB', { hour12: false });
                            return `${ctx.dataset.label}: ${startStr} - ${endStr} (${durMin}m)`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'hour',
                        displayFormats: { hour: 'MMM d HH:mm' },
                        tooltipFormat: 'MMM d HH:mm:ss'
                    },
                    min: cutoff.getTime(),
                    max: nowUtc,
                    grid: { color: '#1f1f35' },
                },
                y: {
                    display: hasBothSources,
                },
            },
        },
    });
}

// --- Chart 2: Timezone History (Stepped Line) ---

function renderTzHistoryChart(tzHistory) {
    const ctx = document.getElementById('tzHistoryChart').getContext('2d');

    if (tzHistoryChartInstance) {
        tzHistoryChartInstance.destroy();
    }

    const data = tzHistory.map(t => ({
        x: t.date,
        y: t.offset_hours,
    }));

    tzHistoryChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'UTC Offset',
                data: data,
                stepped: true,
                borderColor: '#60a5fa',
                backgroundColor: '#60a5fa22',
                fill: true,
                pointRadius: 4,
                pointBackgroundColor: '#60a5fa',
                pointHoverRadius: 6,
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const offset = ctx.parsed.y;
                            const sign = offset >= 0 ? '+' : '';
                            return `UTC${sign}${offset}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'day', displayFormats: { day: 'MMM d' } },
                    grid: { color: '#1f1f35' },
                },
                y: {
                    title: { display: true, text: 'UTC Offset', color: '#666' },
                    min: -12,
                    max: 14,
                    grid: { color: '#1f1f35' },
                    ticks: {
                        callback: function(val) {
                            const sign = val >= 0 ? '+' : '';
                            return `${sign}${val}`;
                        }
                    }
                },
            },
        },
    });
}

// --- Chart 3: Wake-up Pattern (Scatter) ---

function renderWakeupChart(wakeupTimes) {
    const ctx = document.getElementById('wakeupChart').getContext('2d');

    if (wakeupChartInstance) {
        wakeupChartInstance.destroy();
    }

    const data = wakeupTimes.map(w => ({
        x: w.date,
        y: w.hour_utc,
    }));

    // Color points by timezone offset
    const offsets = wakeupTimes.map(w => w.offset);
    const uniqueOffsets = [...new Set(offsets)].sort((a, b) => a - b);
    const colorPalette = ['#f472b6', '#60a5fa', '#4ade80', '#facc15', '#fb923c', '#a78bfa', '#f87171'];
    const offsetColorMap = {};
    uniqueOffsets.forEach((o, i) => {
        offsetColorMap[o] = colorPalette[i % colorPalette.length];
    });

    const pointColors = wakeupTimes.map(w => offsetColorMap[w.offset] || '#888');

    wakeupChartInstance = new Chart(ctx, {
        type: 'scatter',
        data: {
            datasets: [{
                label: 'Wake-up Time (UTC)',
                data: data,
                backgroundColor: pointColors,
                pointRadius: 6,
                pointHoverRadius: 8,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const w = wakeupTimes[ctx.dataIndex];
                            const h = Math.floor(w.hour_utc);
                            const m = Math.round((w.hour_utc - h) * 60);
                            const sign = w.offset >= 0 ? '+' : '';
                            return `${w.date}: ${h}:${m.toString().padStart(2, '0')} UTC (est. UTC${sign}${w.offset})`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'day', displayFormats: { day: 'MMM d' } },
                    grid: { color: '#1f1f35' },
                },
                y: {
                    title: { display: true, text: 'Hour (UTC)', color: '#666' },
                    min: 0,
                    max: 24,
                    grid: { color: '#1f1f35' },
                    ticks: {
                        stepSize: 3,
                        callback: function(val) {
                            return `${val}:00`;
                        }
                    }
                },
            },
        },
    });
}
