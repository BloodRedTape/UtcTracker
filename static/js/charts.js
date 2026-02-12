// NickUtc - Chart.js Chart Definitions (theme-aware)

let timelineChartInstance = null;
let sleepDurationChartInstance = null;
let wakeupChartInstance = null;

// Cached data for re-rendering on theme switch
let cachedTimelineData = null;
let cachedSleepDurationData = null;
let cachedWakeupData = null;

// --- Theme Helpers ---

function getCSSColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function applyChartTheme() {
    Chart.defaults.color = getCSSColor('--muted-foreground');
    Chart.defaults.borderColor = getCSSColor('--border');

    // Re-render all active charts with new theme colors
    if (cachedTimelineData) renderTimelineChart(cachedTimelineData);
    if (cachedSleepDurationData) renderSleepDurationChart(cachedSleepDurationData);
    if (cachedWakeupData) renderWakeupChart(cachedWakeupData);
}

// --- Chart 1: Activity Timeline (Floating Bars) ---

function renderTimelineChart(onlinePeriods) {
    cachedTimelineData = onlinePeriods;
    const ctx = document.getElementById('timelineChart').getContext('2d');

    if (timelineChartInstance) {
        timelineChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const telegramColor = getCSSColor('--telegram');
    const discordColor = getCSSColor('--discord');
    const successColor = getCSSColor('--success');

    // Filter to last 48 hours (use UTC)
    const nowUtc = Date.now();
    const cutoff = new Date(nowUtc - 48 * 60 * 60 * 1000);

    // Split periods by source
    const sourceConfig = {
        telegram: { label: 'Telegram', color: telegramColor, row: 'TG' },
        discord:  { label: 'Discord',  color: discordColor, row: 'DC' },
    };

    // Check which sources are present
    const sources = [...new Set(onlinePeriods.map(p => p.source || 'telegram'))];
    const hasBothSources = sources.length > 1;

    const datasets = [];
    for (const source of sources) {
        const cfg = sourceConfig[source] || { label: source, color: successColor, row: source };
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
                    grid: { color: gridColor },
                },
                y: {
                    display: hasBothSources,
                },
            },
        },
    });
}

// --- Chart 2: Sleep Duration (Bar) ---

function renderSleepDurationChart(sleepPeriods) {
    cachedSleepDurationData = sleepPeriods;
    const ctx = document.getElementById('sleepDurationChart').getContext('2d');

    if (sleepDurationChartInstance) {
        sleepDurationChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const mutedFg = getCSSColor('--muted-foreground');

    const data = sleepPeriods.map(sp => ({
        x: sp.date,
        y: Math.round(sp.gap_hours * 10) / 10,
    }));

    // Color bars by timezone offset (same palette as wake-up pattern)
    const colorPalette = ['#f472b6', '#60a5fa', '#4ade80', '#facc15', '#fb923c', '#a78bfa', '#f87171'];
    const offsets = sleepPeriods.map(sp => sp.estimated_tz_offset);
    const uniqueOffsets = [...new Set(offsets)].sort((a, b) => a - b);
    const offsetColorMap = {};
    uniqueOffsets.forEach((o, i) => {
        offsetColorMap[o] = colorPalette[i % colorPalette.length];
    });
    const barColors = sleepPeriods.map(sp => offsetColorMap[sp.estimated_tz_offset] || mutedFg);

    sleepDurationChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            datasets: [{
                label: 'Sleep Duration',
                data: data,
                backgroundColor: barColors,
                borderRadius: 3,
                borderSkipped: false,
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
                            const h = Math.floor(ctx.parsed.y);
                            const m = Math.round((ctx.parsed.y - h) * 60);
                            return `${h}h ${m}m`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'day', displayFormats: { day: 'MMM d' } },
                    grid: { color: gridColor },
                },
                y: {
                    title: { display: true, text: 'Hours', color: mutedFg },
                    min: 0,
                    grid: { color: gridColor },
                    ticks: {
                        callback: function(val) {
                            return `${val}h`;
                        }
                    }
                },
            },
        },
    });
}

// --- Chart 3: Wake-up Pattern (Scatter) ---

function renderWakeupChart(wakeupTimes) {
    cachedWakeupData = wakeupTimes;
    const ctx = document.getElementById('wakeupChart').getContext('2d');

    if (wakeupChartInstance) {
        wakeupChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const mutedFg = getCSSColor('--muted-foreground');

    const data = wakeupTimes.map(w => ({
        x: w.date,
        y: w.hour_utc,
    }));

    // Auto-zoom: compute Y range from data with 1h padding, minimum 4h span
    const hours = wakeupTimes.map(w => w.hour_utc);
    let yMin = 0, yMax = 24;
    if (hours.length) {
        const dataMin = Math.floor(Math.min(...hours)) - 1;
        const dataMax = Math.ceil(Math.max(...hours)) + 1;
        const span = dataMax - dataMin;
        if (span < 4) {
            const mid = (dataMin + dataMax) / 2;
            yMin = Math.max(0, Math.floor(mid - 2));
            yMax = Math.min(24, Math.ceil(mid + 2));
        } else {
            yMin = Math.max(0, dataMin);
            yMax = Math.min(24, dataMax);
        }
    }

    // Color points by timezone offset
    const offsets = wakeupTimes.map(w => w.offset);
    const uniqueOffsets = [...new Set(offsets)].sort((a, b) => a - b);
    const colorPalette = ['#f472b6', '#60a5fa', '#4ade80', '#facc15', '#fb923c', '#a78bfa', '#f87171'];
    const offsetColorMap = {};
    uniqueOffsets.forEach((o, i) => {
        offsetColorMap[o] = colorPalette[i % colorPalette.length];
    });

    const pointColors = wakeupTimes.map(w => offsetColorMap[w.offset] || mutedFg);

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
                    grid: { color: gridColor },
                },
                y: {
                    title: { display: true, text: 'Hour (UTC)', color: mutedFg },
                    min: yMin,
                    max: yMax,
                    grid: { color: gridColor },
                    ticks: {
                        stepSize: 1,
                        callback: function(val) {
                            return `${val}:00`;
                        }
                    }
                },
            },
        },
    });
}
