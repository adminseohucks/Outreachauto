/* LinkedPilot v2 — Frontend JavaScript */

// --- SSE Activity Feed ---
document.addEventListener('DOMContentLoaded', function() {
    const liveFeed = document.getElementById('live-feed');
    const activityFeed = document.getElementById('activity-list');

    // SSE for live activity ticker
    if (liveFeed) {
        const evtSource = new EventSource('/api/activity-stream');

        evtSource.addEventListener('activity', function(e) {
            try {
                const data = JSON.parse(e.data);
                const time = data.created_at ? data.created_at.split('T')[1]?.substring(0,8) : '';
                const badge = statusBadge(data.status);
                liveFeed.innerHTML = `<span class="time">${time}</span> ${badge} <b>${data.sender_name || ''}</b> ${data.action_type}: ${data.details} ${data.lead_name ? '- ' + data.lead_name : ''}`;

                // Also prepend to activity list if it exists
                if (activityFeed) {
                    const item = document.createElement('div');
                    item.className = 'activity-item';
                    item.innerHTML = `
                        <span class="time">${time}</span>
                        <span class="sender">${data.sender_name || ''}</span>
                        ${badge}
                        <span class="action">${data.action_type}: ${data.details} ${data.lead_name ? '- ' + data.lead_name : ''}</span>
                    `;
                    activityFeed.prepend(item);
                    // Keep only last 15
                    while (activityFeed.children.length > 15) {
                        activityFeed.removeChild(activityFeed.lastChild);
                    }
                }
            } catch(err) {
                console.error('SSE parse error:', err);
            }
        });

        evtSource.onerror = function() {
            liveFeed.textContent = 'Live feed disconnected. Reconnecting...';
        };
    }

    // --- HTMX polling for stats ---
    const statsEl = document.getElementById('live-stats');
    if (statsEl) {
        setInterval(async () => {
            try {
                const resp = await fetch('/api/stats');
                const stats = await resp.json();
                updateStats(stats);
            } catch(e) {}
        }, 5000);
    }
});

function updateStats(stats) {
    const map = {
        'stat-likes': stats.likes_today,
        'stat-comments': stats.comments_today,
        'stat-campaigns': stats.active_campaigns,
        'stat-senders': stats.active_senders,
    };
    for (const [id, val] of Object.entries(map)) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }
    const ts = document.getElementById('stat-timestamp');
    if (ts) ts.textContent = 'Updated: ' + stats.timestamp;
}

function statusBadge(status) {
    const cls = 'badge badge-' + (status || 'pending');
    return `<span class="${cls}">${status || 'unknown'}</span>`;
}

// --- Modal helpers ---
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('active');
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('active');
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});

// --- Select all checkbox ---
function toggleSelectAll(source) {
    const checkboxes = document.querySelectorAll('input[name="lead_ids"]');
    checkboxes.forEach(cb => cb.checked = source.checked);
}

// --- Confirm delete ---
function confirmDelete(msg) {
    return confirm(msg || 'Are you sure you want to delete this?');
}
