// NickUtc - Frontend Application Logic

let selectedUserId = null;
let refreshTimer = null;

// --- Theme Management ---

function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
}

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('nickutc-theme', theme);
    applyChartTheme();
}

function toggleTheme() {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
}

function setupTheme() {
    document.getElementById('themeToggle').addEventListener('click', toggleTheme);

    // Listen for OS preference changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (!localStorage.getItem('nickutc-theme')) {
            setTheme(e.matches ? 'dark' : 'light');
        }
    });
}

// --- Utilities ---

function formatTz(offset) {
    if (offset === null || offset === undefined) return 'N/A';
    const sign = offset >= 0 ? '+' : '-';
    const abs = Math.abs(offset);
    const hours = Math.floor(abs);
    const minutes = Math.round((abs - hours) * 60);
    return minutes ? `UTC${sign}${hours}:${minutes.toString().padStart(2, '0')}` : `UTC${sign}${hours}`;
}

function getUserLocalTime(offset) {
    if (offset === null || offset === undefined) return 'N/A';
    const now = new Date();
    const utcTime = now.getTime();
    const userTime = new Date(utcTime + offset * 60 * 60 * 1000);
    const hours = String(userTime.getUTCHours()).padStart(2, '0');
    const minutes = String(userTime.getUTCMinutes()).padStart(2, '0');
    const seconds = String(userTime.getUTCSeconds()).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
}

function timeAgo(isoStr) {
    if (!isoStr) return 'Never';
    // Use UTC for both to avoid timezone confusion
    const nowUtc = Date.now();
    const thenUtc = new Date(isoStr).getTime();
    const diffMs = nowUtc - thenUtc;
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDays = Math.floor(diffHr / 24);
    return `${diffDays}d ago`;
}

function formatDateTime(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    // Display in local timezone
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function formatHours(h) {
    const hours = Math.floor(h);
    const minutes = Math.round((h - hours) * 60);
    return `${hours}h ${minutes}m`;
}

// --- API ---

async function fetchJSON(url) {
    const res = await fetch(url);
    return res.json();
}

// --- Users Table ---

let cachedUsers = [];

async function loadUsers() {
    const users = await fetchJSON('/api/users');
    cachedUsers = users;
    const tbody = document.getElementById('usersBody');

    if (!users.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No tracked users yet. Waiting for data...</td></tr>';
        return;
    }

    tbody.innerHTML = users.map(u => {
        const userLocalTime = getUserLocalTime(u.current_tz_offset);
        const tzDisplay = u.timezone_display !== 'N/A'
            ? `${u.timezone_display}<br><span class="user-time" data-offset="${u.current_tz_offset}">${userLocalTime}</span>`
            : 'N/A';
        const sourceBadges = [
            u.telegram_id ? `<span class="source-badge tg ${u.telegram_status || ''}" title="Telegram: ${u.telegram_status || 'unknown'}">TG</span>` : '',
            u.discord_id ? `<span class="source-badge dc ${u.discord_status || ''}" title="Discord: ${u.discord_status || 'unknown'}">DC</span>` : '',
        ].filter(Boolean).join(' ');
        return `
            <tr data-uid="${u.user_id}" class="${u.user_id === selectedUserId ? 'active' : ''}" onclick="selectUser(${u.user_id})">
                <td><span class="status-dot ${u.current_status || 'offline'}"></span>${u.current_status || 'unknown'}</td>
                <td>${escapeHtml(u.label)} ${sourceBadges}</td>
                <td>${u.username ? '@' + escapeHtml(u.username) : '-'}</td>
                <td class="tz-offset">${tzDisplay}</td>
                <td>${timeAgo(u.last_event_utc)}</td>
                <td>${u.events_count}</td>
            </tr>
        `;
    }).join('');

    updateSystemTime();
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- User Detail ---

async function selectUser(userId) {
    selectedUserId = userId;

    // Highlight active row
    document.querySelectorAll('#usersTable tbody tr').forEach(tr => {
        tr.classList.toggle('active', parseInt(tr.dataset.uid) === userId);
    });

    // Show detail section
    document.getElementById('detailSection').classList.remove('hidden');

    // Fetch data in parallel
    const [user, sleepPeriods, tzHistory, stats] = await Promise.all([
        fetchJSON(`/api/users/${userId}`),
        fetchJSON(`/api/users/${userId}/sleep-periods`),
        fetchJSON(`/api/users/${userId}/timezone-history`),
        fetchJSON(`/api/users/${userId}/stats`),
    ]);

    // Update header
    document.getElementById('detailName').textContent = user.label;
    document.getElementById('detailUsername').textContent = user.username ? '@' + user.username : '';

    cachedUserOffset = user.current_tz_offset;
    const userLocalTime = getUserLocalTime(user.current_tz_offset);
    const tzText = user.timezone_display !== 'N/A'
        ? `${user.timezone_display} • ${userLocalTime}`
        : 'N/A';
    document.getElementById('detailTz').textContent = tzText;

    const statusBadge = document.getElementById('detailStatus');
    statusBadge.textContent = user.current_status || 'unknown';
    statusBadge.className = 'status-badge ' + (user.current_status || 'offline');

    // Render charts
    renderTimelineChart(stats.online_periods || []);
    renderSleepDurationChart(sleepPeriods || []);
    renderWakeupChart(stats.wakeup_times || []);

    // Render sleep periods table (latest first)
    const sleepBody = document.getElementById('sleepBody');
    const sortedSleep = [...sleepPeriods].reverse();
    sleepBody.innerHTML = sortedSleep.slice(0, 50).map(sp => `
        <tr>
            <td>${sp.date}</td>
            <td>${formatDateTime(sp.offline_at_utc)}</td>
            <td>${formatDateTime(sp.online_at_utc)}</td>
            <td>${formatHours(sp.gap_hours)}</td>
            <td class="tz-offset">${formatTz(sp.estimated_tz_offset)}</td>
        </tr>
    `).join('') || '<tr><td colspan="5" class="loading">No sleep periods detected yet</td></tr>';

    // Render daily timezone table (latest first)
    const tzBody = document.getElementById('tzBody');
    const sortedTz = [...(tzHistory || [])].reverse();
    tzBody.innerHTML = sortedTz.slice(0, 50).map(dt => `
        <tr>
            <td>${dt.date}</td>
            <td class="tz-offset">${formatTz(dt.offset_hours)}</td>
            <td>${formatDateTime(dt.wakeup_utc)}</td>
        </tr>
    `).join('') || '<tr><td colspan="3" class="loading">No timezone data yet</td></tr>';
}

// --- System Time Display ---

function getLocalTzString() {
    const now = new Date();
    const offsetMinutes = -now.getTimezoneOffset();
    const offsetHours = Math.floor(Math.abs(offsetMinutes) / 60);
    const offsetMins = Math.abs(offsetMinutes) % 60;
    const offsetSign = offsetMinutes >= 0 ? '+' : '-';
    return offsetMins > 0
        ? `UTC${offsetSign}${offsetHours}:${String(offsetMins).padStart(2, '0')}`
        : `UTC${offsetSign}${offsetHours}`;
}

function updateSystemTime() {
    const now = new Date();
    const localTime = now.toLocaleTimeString('en-GB', { hour12: false });
    const tzStr = getLocalTzString();

    document.getElementById('lastUpdated').textContent = `${localTime} (${tzStr})`;
}

// --- Auto-refresh ---

function setupAutoRefresh() {
    const checkbox = document.getElementById('autoRefresh');
    checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
            startRefresh();
        } else {
            stopRefresh();
        }
    });
    startRefresh();
}

function updateUserLocalTimes() {
    // Update local times in the users table
    document.querySelectorAll('#usersTable .user-time').forEach(timeSpan => {
        const offset = parseFloat(timeSpan.dataset.offset);
        if (!isNaN(offset)) {
            timeSpan.textContent = getUserLocalTime(offset);
        }
    });

    // Update local time in detail header
    const detailTz = document.getElementById('detailTz');
    if (detailTz && selectedUserId && cachedUserOffset !== null) {
        const userLocalTime = getUserLocalTime(cachedUserOffset);
        const tzParts = detailTz.textContent.split(' • ');
        if (tzParts.length === 2) {
            detailTz.textContent = `${tzParts[0]} • ${userLocalTime}`;
        }
    }
}

let cachedUserOffset = null;

function startRefresh() {
    stopRefresh();
    refreshTimer = setInterval(async () => {
        await loadUsers();
        if (selectedUserId) {
            await selectUser(selectedUserId);
        }
    }, 60000);

    // Update system time and user local times every second
    setInterval(() => {
        updateSystemTime();
        updateUserLocalTimes();
    }, 1000);
}

function stopRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
}

// --- Init ---

document.addEventListener('DOMContentLoaded', async () => {
    setupTheme();
    applyChartTheme();
    updateSystemTime();
    await loadUsers();
    setupAutoRefresh();
});
