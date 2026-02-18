// NickUtc - Chart.js Chart Definitions (theme-aware)

let timelineChartInstance = null;
let sleepDurationChartInstance = null;
let wakeupChartInstance = null;
let sleepPatternChartInstance = null;
let awakeDurationChartInstance = null;

// Cached data for re-rendering on theme switch
let cachedTimelineData = null;
let cachedSleepDurationData = null;
let cachedWakeupData = null;
let cachedSleepPatternData = null;
let cachedAwakeDurationData = null;

// --- Weekend Highlight Plugin ---

const weekendHighlightPlugin = {
    id: 'weekendHighlight',
    beforeDraw(chart) {
        const xScale = chart.scales.x;
        if (!xScale || xScale.type !== 'time') return;

        const { ctx, chartArea: { top, bottom } } = chart;

        const msPerDay = 86400000;
        const halfDay = msPerDay / 2;

        // Scan visible range day by day, centered on noon UTC to avoid edge issues
        const startMs = xScale.min - msPerDay;
        const endMs = xScale.max + msPerDay;

        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        ctx.save();
        ctx.fillStyle = isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)';

        // Highlight centered on midnight UTC (the point position for each date)
        const d = new Date(startMs);
        d.setUTCHours(0, 0, 0, 0);

        while (d.getTime() <= endMs) {
            const day = d.getUTCDay(); // 0=Sun, 6=Sat
            if (day === 0 || day === 6) {
                const midnight = d.getTime();
                const left = Math.max(xScale.getPixelForValue(midnight - halfDay), xScale.left);
                const right = Math.min(xScale.getPixelForValue(midnight + halfDay), xScale.right);
                if (right > left) {
                    ctx.fillRect(left, top, right - left, bottom - top);
                }
            }
            d.setUTCDate(d.getUTCDate() + 1);
        }
        ctx.restore();
    }
};

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
    if (cachedSleepPatternData) renderSleepPatternChart(cachedSleepPatternData);
    if (cachedAwakeDurationData) renderAwakeDurationChart(cachedAwakeDurationData);
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
        plugins: [weekendHighlightPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const sp = sleepPeriods[ctx.dataIndex];
                            const h = Math.floor(ctx.parsed.y);
                            const m = Math.round((ctx.parsed.y - h) * 60);
                            const sleepStr = new Date(sp.offline_at_utc).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                            const wakeStr = new Date(sp.online_at_utc).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                            return [
                                `${h}h ${m}m`,
                                `${sleepStr} → ${wakeStr}`,
                            ];
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
// Note: Sleep Pattern (Chart 4) mirrors this with sleep (offline) times

function renderWakeupChart(wakeupTimes) {
    cachedWakeupData = wakeupTimes;
    const ctx = document.getElementById('wakeupChart').getContext('2d');

    if (wakeupChartInstance) {
        wakeupChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const mutedFg = getCSSColor('--muted-foreground');

    // Convert UTC hours to browser local timezone
    const browserOffsetHours = -new Date().getTimezoneOffset() / 60;
    const toLocal = (utcHour) => {
        let local = utcHour + browserOffsetHours;
        if (local < 0) local += 24;
        if (local >= 24) local -= 24;
        return local;
    };

    const data = wakeupTimes.map(w => ({
        x: w.date,
        y: toLocal(w.hour_utc),
    }));

    // Auto-zoom: compute Y range from data with 1h padding, minimum 4h span
    const hours = data.map(d => d.y);
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
                label: 'Wake-up Time (local)',
                data: data,
                backgroundColor: pointColors,
                pointRadius: 6,
                pointHoverRadius: 8,
            }],
        },
        plugins: [weekendHighlightPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const w = wakeupTimes[ctx.dataIndex];
                            const localHour = toLocal(w.hour_utc);
                            const h = Math.floor(localHour);
                            const m = Math.round((localHour - h) * 60);
                            const sign = w.offset >= 0 ? '+' : '';
                            return `${w.date}: ${h}:${m.toString().padStart(2, '0')} (est. UTC${sign}${w.offset})`;
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
                    title: { display: true, text: 'Hour (local)', color: mutedFg },
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

// --- Chart 4: Sleep Pattern (Scatter) ---
// sleepPeriods: array of {date, offline_at_utc, online_at_utc, gap_hours, estimated_tz_offset}

function renderSleepPatternChart(sleepPeriods) {
    cachedSleepPatternData = sleepPeriods;
    const ctx = document.getElementById('sleepPatternChart').getContext('2d');

    if (sleepPatternChartInstance) {
        sleepPatternChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const mutedFg = getCSSColor('--muted-foreground');

    const browserOffsetHours = -new Date().getTimezoneOffset() / 60;
    const toLocal = (utcHour) => {
        let local = utcHour + browserOffsetHours;
        if (local < 0) local += 24;
        if (local >= 24) local -= 24;
        return local;
    };

    // Extract sleep (offline) times from each sleep period
    const sleepTimes = sleepPeriods
        .filter(sp => sp.offline_at_utc)
        .map(sp => {
            const d = new Date(sp.offline_at_utc);
            const utcHour = d.getUTCHours() + d.getUTCMinutes() / 60;
            return {
                date: sp.date,
                utcHour,
                offset: sp.estimated_tz_offset,
                offline_at_utc: sp.offline_at_utc,
                online_at_utc: sp.online_at_utc,
            };
        });

    const data = sleepTimes.map(s => ({
        x: s.date,
        y: toLocal(s.utcHour),
    }));

    // Auto-zoom Y axis
    const hours = data.map(d => d.y);
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
    const offsets = sleepTimes.map(s => s.offset);
    const uniqueOffsets = [...new Set(offsets)].sort((a, b) => a - b);
    const colorPalette = ['#f472b6', '#60a5fa', '#4ade80', '#facc15', '#fb923c', '#a78bfa', '#f87171'];
    const offsetColorMap = {};
    uniqueOffsets.forEach((o, i) => {
        offsetColorMap[o] = colorPalette[i % colorPalette.length];
    });
    const pointColors = sleepTimes.map(s => offsetColorMap[s.offset] || mutedFg);

    sleepPatternChartInstance = new Chart(ctx, {
        type: 'scatter',
        data: {
            datasets: [{
                label: 'Sleep Time (local)',
                data: data,
                backgroundColor: pointColors,
                pointStyle: 'triangle',
                pointRadius: 7,
                pointHoverRadius: 9,
            }],
        },
        plugins: [weekendHighlightPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const s = sleepTimes[ctx.dataIndex];
                            const localHour = toLocal(s.utcHour);
                            const h = Math.floor(localHour);
                            const m = Math.round((localHour - h) * 60);
                            const sign = s.offset >= 0 ? '+' : '';
                            return `${s.date}: ${h}:${m.toString().padStart(2, '0')} (est. UTC${sign}${s.offset})`;
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
                    title: { display: true, text: 'Hour (local)', color: mutedFg },
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

// --- Chart 5: Awake Duration (Bar) ---
// sleepPeriods: sorted oldest-first; awake = time between online_at of period[i] and offline_at of period[i+1]

function renderAwakeDurationChart(sleepPeriods) {
    cachedAwakeDurationData = sleepPeriods;
    const ctx = document.getElementById('awakeDurationChart').getContext('2d');

    if (awakeDurationChartInstance) {
        awakeDurationChartInstance.destroy();
    }

    const gridColor = getCSSColor('--chart-grid');
    const mutedFg = getCSSColor('--muted-foreground');

    // Compute awake durations: gap between online_at[i] and offline_at[i+1]
    const sorted = [...sleepPeriods].sort((a, b) => a.date < b.date ? -1 : 1);
    const awakeData = [];
    for (let i = 0; i < sorted.length - 1; i++) {
        const wakeTime = new Date(sorted[i].online_at_utc).getTime();
        const nextSleepTime = new Date(sorted[i + 1].offline_at_utc).getTime();
        const awakeHours = (nextSleepTime - wakeTime) / 3600000;
        if (awakeHours > 0 && awakeHours < 36) { // sanity filter
            awakeData.push({
                date: sorted[i + 1].date,
                awakeHours: Math.round(awakeHours * 10) / 10,
                offset: sorted[i + 1].estimated_tz_offset,
                wakeStart: sorted[i].online_at_utc,
                sleepStart: sorted[i + 1].offline_at_utc,
            });
        }
    }

    const data = awakeData.map(d => ({ x: d.date, y: d.awakeHours }));

    // Color bars by timezone offset
    const colorPalette = ['#f472b6', '#60a5fa', '#4ade80', '#facc15', '#fb923c', '#a78bfa', '#f87171'];
    const offsets = awakeData.map(d => d.offset);
    const uniqueOffsets = [...new Set(offsets)].sort((a, b) => a - b);
    const offsetColorMap = {};
    uniqueOffsets.forEach((o, i) => {
        offsetColorMap[o] = colorPalette[i % colorPalette.length];
    });
    const barColors = awakeData.map(d => offsetColorMap[d.offset] || mutedFg);

    awakeDurationChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            datasets: [{
                label: 'Awake Duration',
                data: data,
                backgroundColor: barColors,
                borderRadius: 3,
                borderSkipped: false,
            }],
        },
        plugins: [weekendHighlightPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const d = awakeData[ctx.dataIndex];
                            const h = Math.floor(ctx.parsed.y);
                            const m = Math.round((ctx.parsed.y - h) * 60);
                            const wakeStr = new Date(d.wakeStart).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                            const sleepStr = new Date(d.sleepStart).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                            return [
                                `${d.date}: awake ${h}h ${m}m`,
                                `${wakeStr} → ${sleepStr}`,
                            ];
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
