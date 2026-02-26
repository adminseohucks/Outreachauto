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
    if (ts) {
        const displayTime = stats.timestamp || new Date().toLocaleTimeString();
        ts.textContent = 'Updated: ' + displayTime;
    }
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

// --- CSV Upload with Header Matching ---
const CSV_DB_FIELDS = [
    { value: '', label: '-- Skip this column --' },
    { value: 'profile_url', label: 'Profile URL (required)' },
    { value: 'full_name', label: 'Full Name' },
    { value: 'first_name', label: 'First Name' },
    { value: 'headline', label: 'Headline' },
    { value: 'company', label: 'Company' },
    { value: 'location', label: 'Location' },
];

// Auto-match: map common CSV header names to DB fields
const CSV_AUTO_MAP = {
    'profile_url': 'profile_url', 'profileurl': 'profile_url', 'profile url': 'profile_url',
    'linkedin_url': 'profile_url', 'linkedin url': 'profile_url', 'linkedinurl': 'profile_url',
    'url': 'profile_url', 'link': 'profile_url', 'linkedin_profile_url': 'profile_url',
    'linkedin profile url': 'profile_url', 'person linkedin url': 'profile_url',
    'full_name': 'full_name', 'fullname': 'full_name', 'full name': 'full_name', 'name': 'full_name',
    'contact name': 'full_name', 'lead name': 'full_name',
    'first_name': 'first_name', 'firstname': 'first_name', 'first name': 'first_name',
    'first': 'first_name', 'given name': 'first_name',
    'headline': 'headline', 'title': 'headline', 'job_title': 'headline', 'job title': 'headline',
    'position': 'headline', 'designation': 'headline',
    'company': 'company', 'company_name': 'company', 'company name': 'company',
    'organization': 'company', 'org': 'company', 'employer': 'company',
    'location': 'location', 'city': 'location', 'region': 'location',
    'address': 'location', 'geo': 'location', 'country': 'location',
};

let _csvFile = null;
let _csvHeaders = [];

function csvFileSelected(input) {
    const file = input.files[0];
    if (!file) return;
    _csvFile = file;

    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const firstLine = text.split(/\r?\n/)[0];
        // Parse CSV header (handle quoted fields)
        _csvHeaders = firstLine.split(',').map(h => h.trim().replace(/^["']|["']$/g, ''));

        const rowCount = text.split(/\r?\n/).filter(l => l.trim()).length - 1;
        document.getElementById('csv-file-info').textContent = file.name + ' — ' + rowCount + ' rows detected';

        // Build mapping table
        const tbody = document.getElementById('csv-mapping-body');
        tbody.innerHTML = '';

        _csvHeaders.forEach((header, idx) => {
            const tr = document.createElement('tr');

            // CSV column name
            const tdCsv = document.createElement('td');
            tdCsv.textContent = header;
            tdCsv.style.fontWeight = '600';
            tr.appendChild(tdCsv);

            // DB field dropdown
            const tdDb = document.createElement('td');
            const select = document.createElement('select');
            select.id = 'csv-map-' + idx;
            select.style.marginBottom = '0';

            CSV_DB_FIELDS.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.value;
                opt.textContent = f.label;
                select.appendChild(opt);
            });

            // Auto-match based on CSV header name
            const normalized = header.toLowerCase().trim();
            if (CSV_AUTO_MAP[normalized]) {
                select.value = CSV_AUTO_MAP[normalized];
            }

            tdDb.appendChild(select);
            tr.appendChild(tdDb);
            tbody.appendChild(tr);
        });

        // Show step 2
        document.getElementById('csv-step-1').style.display = 'none';
        document.getElementById('csv-step-2').style.display = 'block';
        document.getElementById('csv-modal-title').textContent = 'Match Your Data';
        document.getElementById('csv-mapping-error').style.display = 'none';
    };
    reader.readAsText(file);
}

function csvSubmitMapped(listId) {
    // Collect mapping
    const mapping = {};
    let hasProfileUrl = false;

    _csvHeaders.forEach((header, idx) => {
        const select = document.getElementById('csv-map-' + idx);
        const dbField = select.value;
        if (dbField) {
            mapping[header] = dbField;
            if (dbField === 'profile_url') hasProfileUrl = true;
        }
    });

    // Validate: profile_url is required
    if (!hasProfileUrl) {
        const errEl = document.getElementById('csv-mapping-error');
        errEl.textContent = 'You must map at least one column to "Profile URL (required)".';
        errEl.style.display = 'block';
        return;
    }

    // Check for duplicate DB field mappings
    const dbValues = Object.values(mapping);
    const seen = new Set();
    for (const v of dbValues) {
        if (seen.has(v)) {
            const errEl = document.getElementById('csv-mapping-error');
            errEl.textContent = 'Each database field can only be mapped once. Duplicate: ' + v;
            errEl.style.display = 'block';
            return;
        }
        seen.add(v);
    }

    // Show progress
    document.getElementById('csv-step-2').style.display = 'none';
    document.getElementById('csv-step-3').style.display = 'block';
    document.getElementById('csv-modal-title').textContent = 'Importing...';

    // Build FormData with file + mapping JSON
    const formData = new FormData();
    formData.append('file', _csvFile);
    formData.append('column_mapping', JSON.stringify(mapping));

    fetch('/lists/' + listId + '/upload-csv', {
        method: 'POST',
        body: formData,
    })
    .then(resp => resp.json())
    .then(data => {
        const msg = document.getElementById('csv-progress-msg');
        msg.removeAttribute('aria-busy');
        if (data.success) {
            msg.innerHTML = '<span style="color: var(--lp-green);">&#10003;</span> Imported <strong>' + data.imported + '</strong> leads. Skipped: ' + data.skipped + '.';
            if (data.errors && data.errors.length > 0) {
                msg.innerHTML += '<br><small style="color: var(--lp-red, #e53e3e);">' + data.errors.length + ' error(s)</small>';
            }
            // Reload page after short delay
            setTimeout(() => { window.location.reload(); }, 1500);
        } else {
            msg.innerHTML = '<span style="color: var(--lp-red, #e53e3e);">Import failed:</span> ' + (data.errors ? data.errors.join(', ') : 'Unknown error');
        }
    })
    .catch(err => {
        const msg = document.getElementById('csv-progress-msg');
        msg.removeAttribute('aria-busy');
        msg.innerHTML = '<span style="color: var(--lp-red, #e53e3e);">Upload failed:</span> ' + err.message;
    });
}

function csvUploadReset() {
    _csvFile = null;
    _csvHeaders = [];
    document.getElementById('csv-step-1').style.display = 'block';
    document.getElementById('csv-step-2').style.display = 'none';
    document.getElementById('csv-step-3').style.display = 'none';
    document.getElementById('csv-modal-title').textContent = 'Upload CSV';
    document.getElementById('csv-mapping-error').style.display = 'none';
    const fileInput = document.getElementById('csv-file');
    if (fileInput) fileInput.value = '';
    closeModal('upload-csv-modal');
}
