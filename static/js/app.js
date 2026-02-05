// NickUtc - Frontend Application Logic

let selectedUserId = null;
let refreshTimer = null;

// --- Utilities ---

function formatTz(offset) {
    if (offset === null || offset === undefined) return 'N/A';
    const sign = offset >= 0 ? '+' : '-';
    const abs = Math.abs(offset);
    const hours = Math.floor(abs);
    const minutes = Math.round((abs - hours) * 60);
    return minutes ? `UTC${sign}${hours}:${minutes.toString().padStart(2, '0')}` : `UTC${sign}${hours}`;
}

function timeAgo(isoStr) {
    if (!isoStr) return 'Never';
    const now = new Date();
    const then = new Date(isoStr);
    const diffMs = now - then;
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
    return d.toISOString().replace('T', ' ').replace('Z', '').slice(0, 19);
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

async function loadUsers() {
    const users = await fetchJSON('/api/users');
    const tbody = document.getElementById('usersBody');

    if (!users.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No tracked users yet. Waiting for data...</td></tr>';
        return;
    }

    tbody.innerHTML = users.map(u => `
        <tr data-uid="${u.user_id}" class="${u.user_id === selectedUserId ? 'active' : ''}" onclick="selectUser(${u.user_id})">
            <td><span class="status-dot ${u.current_status || 'offline'}"></span>${u.current_status || 'unknown'}</td>
            <td>${escapeHtml(u.label)}</td>
            <td>${u.username ? '@' + escapeHtml(u.username) : '-'}</td>
            <td class="tz-offset">${u.timezone_display}</td>
            <td>${timeAgo(u.last_event_utc)}</td>
            <td>${u.events_count}</td>
        </tr>
    `).join('');

    document.getElementById('lastUpdated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
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
    document.getElementById('detailTz').textContent = user.timezone_display || 'N/A';

    const statusBadge = document.getElementById('detailStatus');
    statusBadge.textContent = user.current_status || 'unknown';
    statusBadge.className = 'status-badge ' + (user.current_status || 'offline');

    // Render charts
    renderTimelineChart(stats.online_periods || []);
    renderTzHistoryChart(tzHistory || []);
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

function startRefresh() {
    stopRefresh();
    refreshTimer = setInterval(async () => {
        await loadUsers();
        if (selectedUserId) {
            await selectUser(selectedUserId);
        }
    }, 60000);
}

function stopRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
}

// --- Init ---

document.addEventListener('DOMContentLoaded', async () => {
    await loadUsers();
    setupAutoRefresh();
});
