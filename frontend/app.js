// ═══════════════════════════════════════════
//  AEROFLEET — COMMAND CENTER
// ═══════════════════════════════════════════

const CREDENTIALS = {
    'admin1969': { pass: 'saxophone@1969', role: 'admin', truck: null },
    'cascadia1969': { pass: 'freight1969', role: 'driver', truck: 'TRK-001' },
    'driver1969': { pass: 'volvo1969', role: 'driver', truck: 'TRK-002' },
    'peter1969': { pass: 'bilt1969', role: 'driver', truck: 'TRK-003' }
};

const TRUCK_META = {
    'TRK-001': { model: 'Freightliner Cascadia', color: '#BFFF00', cssClass: 'lime' },
    'TRK-002': { model: 'Volvo VNL', color: '#00FFFF', cssClass: 'cyan' },
    'TRK-003': { model: 'Peterbilt 579', color: '#FF006E', cssClass: 'pink' }
};

let currentUserRole = null;
let activeTruck = 'TRK-001';
let mqttClient = null;
let map = null;
let truckMarkers = {};  // { truck_id: L.marker }
let telemetryChart = null;
let fetchInterval = null;
let rollupInterval = null;
let latestVideoUrls = {};  // { truck_id: url }

const maxChartDataPoints = 60;
const chartData = {
    labels: [],
    datasets: [
        { label: 'Speed (km/h)', data: [], borderColor: '#BFFF00', backgroundColor: 'rgba(191,255,0,0.05)', fill: true, tension: 0.3, borderWidth: 1.5, pointRadius: 0 },
        { label: 'RPM', data: [], borderColor: '#00FFFF', backgroundColor: 'rgba(0,255,255,0.05)', fill: true, tension: 0.3, borderWidth: 1.5, pointRadius: 0, yAxisID: 'y1' }
    ]
};

// ─── MAP ───
function initMap() {
    map = L.map('map', {
        zoomControl: false,
        attributionControl: false
    }).setView([20.0, 78.5], 5);

    // Dark basemap
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        subdomains: 'abcd'
    }).addTo(map);

    L.control.zoom({ position: 'bottomright' }).addTo(map);

    // Create markers for all 3 trucks
    Object.keys(TRUCK_META).forEach(truckId => {
        const meta = TRUCK_META[truckId];
        const icon = L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="truck-marker ${meta.cssClass} ${truckId === activeTruck ? 'selected' : ''}"></div>`,
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });

        const marker = L.marker([13.07 + Math.random() * 2, 77.80 + Math.random() * 2], { icon: icon })
            .addTo(map);

        // Tooltip
        marker.bindTooltip(truckId, {
            permanent: true,
            direction: 'top',
            offset: [0, -12],
            className: 'truck-tooltip'
        });

        // Click handler — switch active truck
        marker.on('click', () => {
            setActiveTruck(truckId);
        });

        truckMarkers[truckId] = marker;
    });
}

function setActiveTruck(truckId) {
    activeTruck = truckId;
    const meta = TRUCK_META[truckId];

    // Update selected truck info overlay
    document.getElementById('selected-truck-name').textContent = truckId;
    document.getElementById('selected-truck-model').textContent = meta.model;

    // Update marker styles
    Object.keys(truckMarkers).forEach(id => {
        const m = truckMarkers[id];
        const mMeta = TRUCK_META[id];
        const icon = L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="truck-marker ${mMeta.cssClass} ${id === truckId ? 'selected' : ''}"></div>`,
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });
        m.setIcon(icon);
    });

    // Reset chart
    chartData.labels = [];
    chartData.datasets[0].data = [];
    chartData.datasets[1].data = [];
    if (telemetryChart) telemetryChart.update();

    // Fetch rollups for new truck
    fetchRollups();
}

// ─── CHART ───
function initChart() {
    const ctx = document.getElementById('telemetryChart').getContext('2d');

    Chart.defaults.color = '#555555';
    Chart.defaults.font.family = "'JetBrains Mono', monospace";
    Chart.defaults.font.size = 10;

    telemetryChart = new Chart(ctx, {
        type: 'line',
        data: chartData,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'top', labels: { boxWidth: 8, usePointStyle: true, padding: 12, font: { size: 10 } } },
                tooltip: {
                    backgroundColor: '#151515',
                    titleColor: '#E8E8E8',
                    bodyColor: '#888888',
                    borderColor: '#333333',
                    borderWidth: 1,
                    cornerRadius: 0,
                    titleFont: { family: "'JetBrains Mono', monospace", size: 10 },
                    bodyFont: { family: "'JetBrains Mono', monospace", size: 10 }
                }
            },
            scales: {
                x: { grid: { color: '#1a1a1a' }, ticks: { maxTicksLimit: 8, font: { size: 9 } } },
                y: { type: 'linear', display: true, position: 'left', grid: { color: '#1a1a1a' }, ticks: { font: { size: 9 } } },
                y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { font: { size: 9 } } }
            }
        }
    });
}

// ─── HELPERS ───
function formatTime(date) {
    return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}:${date.getSeconds().toString().padStart(2, '0')}`;
}

function formatAlertTime(alertData) {
    let d;
    if (alertData.time) {
        d = new Date(alertData.time);
        // If the DB returns a timestamp without timezone info, it's UTC
        if (typeof alertData.time === 'string' && !alertData.time.includes('+') && !alertData.time.includes('Z')) {
            d = new Date(alertData.time + 'Z');
        }
    } else {
        d = new Date();
    }
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const day = d.getDate();
    const mon = months[d.getMonth()];
    const h = d.getHours().toString().padStart(2, '0');
    const m = d.getMinutes().toString().padStart(2, '0');
    const s = d.getSeconds().toString().padStart(2, '0');
    return `${day} ${mon}, ${h}:${m}:${s}`;
}

// ─── GAUGE UPDATE ───
function updateGauges(data) {
    document.getElementById('val-rpm').textContent = Math.round(data.rpm);
    document.getElementById('val-speed').textContent = Math.round(data.speed_kmh);
    document.getElementById('val-coolant').textContent = Math.round(data.coolant_temp_f);
    document.getElementById('val-oil').textContent = Math.round(data.oil_pressure_psi);
    document.getElementById('val-boost').textContent = Math.round(data.boost_pressure_psi);
    document.getElementById('val-fuel').textContent = data.fuel_rate_gal_hr.toFixed(1);

    // Highlight speed if over limit
    const speedEl = document.getElementById('val-speed');
    if (data.speed_kmh > 85) {
        speedEl.classList.add('alert-value');
    } else {
        speedEl.classList.remove('alert-value');
    }
}

// ─── CHART UPDATE ───
function updateChart(data) {
    const timeStr = formatTime(new Date());
    chartData.labels.push(timeStr);
    chartData.datasets[0].data.push(data.speed_kmh);
    chartData.datasets[1].data.push(data.rpm);

    if (chartData.labels.length > maxChartDataPoints) {
        chartData.labels.shift();
        chartData.datasets[0].data.shift();
        chartData.datasets[1].data.shift();
    }

    telemetryChart.update('none');
}

// ─── ALERT FEED ───
function addAlertToFeed(alertData) {
    const feed = document.getElementById('alert-feed');
    // Remove "no alerts" message
    const noMsg = feed.querySelector('.no-alerts-msg');
    if (noMsg) noMsg.remove();

    const div = document.createElement('div');
    const sevClass = (alertData.severity || 'low').toLowerCase();
    div.className = `alert-item severity-${sevClass}`;
    div.setAttribute('data-truck-id', alertData.truck_id || activeTruck);
    div.setAttribute('data-video', alertData.video_path || '');

    const truckId = alertData.truck_id || activeTruck;
    const timeStr = formatAlertTime(alertData);

    div.innerHTML = `
        <div class="alert-summary">
            <span class="alert-severity ${sevClass}">${alertData.severity || 'LOW'}</span>
            <span class="alert-type">${alertData.type || 'Alert'}</span>
            <span class="alert-truck-badge">${truckId}</span>
            <span class="alert-time">${timeStr}</span>
        </div>
        <div class="alert-detail">
            <div class="alert-description">${alertData.description || ''}</div>
            <div class="video-grid-placeholder"></div>
        </div>
    `;

    // Click to expand/collapse
    div.addEventListener('click', () => {
        const wasExpanded = div.classList.contains('expanded');
        // Collapse all first
        feed.querySelectorAll('.alert-item.expanded').forEach(el => el.classList.remove('expanded'));

        if (!wasExpanded) {
            div.classList.add('expanded');
            // Switch to this alert's truck
            const alertTruck = div.getAttribute('data-truck-id');
            if (alertTruck && alertTruck !== activeTruck) {
                setActiveTruck(alertTruck);
            }
            // Load video if available
            const videoPath = div.getAttribute('data-video');
            const placeholder = div.querySelector('.video-grid-placeholder');
            if (videoPath && videoPath !== '' && videoPath !== 'null') {
                placeholder.innerHTML = buildVideoGrid(videoPath);
                // Auto-play videos
                placeholder.querySelectorAll('video').forEach(v => v.play().catch(() => {}));
            } else {
                // Check if we have a latest video for this truck
                const latestVid = latestVideoUrls[alertTruck];
                if (latestVid) {
                    placeholder.innerHTML = buildVideoGrid(latestVid);
                    placeholder.querySelectorAll('video').forEach(v => v.play().catch(() => {}));
                } else {
                    placeholder.innerHTML = '<div style="color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:2px;padding:20px 0;text-align:center;">No footage available</div>';
                }
            }
        }
    });

    feed.prepend(div);

    // Cap at 50 alerts
    while (feed.children.length > 50) {
        feed.removeChild(feed.lastChild);
    }
}

function buildVideoGrid(videoUrl) {
    let html = '<div class="video-grid">';
    for (let i = 0; i < 6; i++) {
        html += `<video src="${videoUrl}" muted playsinline ${i === 5 ? 'controls' : ''}></video>`;
    }
    html += '</div>';
    return html;
}

// ─── MQTT ───
function connectMQTT() {
    const host = window.location.hostname || 'localhost';
    const brokerUrl = `ws://${host}:8083/mqtt`;
    console.log(`Connecting to MQTT: ${brokerUrl}`);
    mqttClient = mqtt.connect(brokerUrl);

    mqttClient.on('connect', () => {
        console.log('MQTT Connected');
        // Always subscribe to all trucks
        mqttClient.subscribe('truck/+/telemetry');
        mqttClient.subscribe('truck/+/emergency/video_ready');
    });

    mqttClient.on('message', (topic, message) => {
        try {
            if (topic.endsWith('/telemetry')) {
                const data = JSON.parse(message.toString());
                const truckId = data.truck_id;

                // Update map marker position for this truck
                if (truckMarkers[truckId] && data.lat && data.lng) {
                    truckMarkers[truckId].setLatLng(new L.LatLng(data.lat, data.lng));
                }

                // Only update gauges and chart for active truck
                if (truckId === activeTruck) {
                    updateGauges(data);
                    updateChart(data);
                }

            } else if (topic.endsWith('/emergency/video_ready')) {
                const videoUrl = message.toString();
                // Extract truck ID from topic
                const parts = topic.split('/');
                const truckId = parts[1];
                latestVideoUrls[truckId] = videoUrl;

                const statusEl = document.getElementById('upload-status');
                statusEl.textContent = `Video received for ${truckId}`;
                statusEl.style.color = '#00FF88';
                setTimeout(() => { statusEl.textContent = ''; }, 5000);
            }
        } catch (e) {
            console.error('MQTT message error', e);
        }
    });
}

// ─── EMERGENCY BUTTON ───
document.getElementById('emergency-btn').addEventListener('click', async () => {
    const btn = document.getElementById('emergency-btn');
    const alertSelector = document.getElementById('alert-type-selector');
    const [severity, type] = alertSelector.value.split('|');

    btn.classList.add('active-alert');

    const statusEl = document.getElementById('upload-status');
    if (severity === 'CRITICAL' || severity === 'HIGH') {
        statusEl.textContent = 'Triggering camera... (30s)';
        statusEl.style.color = '#FFB800';
    } else {
        statusEl.textContent = 'Alert raised';
        statusEl.style.color = '#00FF88';
    }

    const desc = `Manual Alert: ${type} raised from dashboard.`;

    try {
        await fetch('/api/alerts/emergency', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                truck_id: activeTruck,
                type: type,
                severity: severity,
                description: desc
            })
        });
        addAlertToFeed({ type, severity, description: desc, truck_id: activeTruck });

        if (severity !== 'CRITICAL' && severity !== 'HIGH') {
            setTimeout(() => { btn.classList.remove('active-alert'); }, 2000);
        }
    } catch (e) {
        console.error('Failed to trigger emergency', e);
        statusEl.textContent = 'Failed';
        statusEl.style.color = '#FF2D2D';
        btn.classList.remove('active-alert');
    }
});

// ─── FETCH ALERTS (all trucks) ───
function startFetchingAlerts() {
    if (fetchInterval) clearInterval(fetchInterval);
    fetchInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/alerts?limit=20');
            const alerts = await res.json();
            if (Array.isArray(alerts)) {
                const feed = document.getElementById('alert-feed');
                feed.innerHTML = '';
                if (alerts.length === 0) {
                    feed.innerHTML = '<div class="no-alerts-msg">No active alerts</div>';
                } else {
                    alerts.forEach(addAlertToFeed);
                }
            }
        } catch (e) {}
    }, 5000);
}

// ─── ROLLUPS ───
async function fetchRollups() {
    if (!activeTruck) return;
    try {
        const res1m = await fetch(`/api/telemetry/rollup/1m?truck_id=${activeTruck}`);
        const data1m = await res1m.json();
        if (data1m && data1m.avg_speed_kmh !== undefined) {
            document.getElementById('rollup-1m-speed').textContent = Math.round(data1m.avg_speed_kmh);
            document.getElementById('rollup-1m-brake').textContent = data1m.max_brake_g.toFixed(2);
        } else {
            document.getElementById('rollup-1m-speed').textContent = '--';
            document.getElementById('rollup-1m-brake').textContent = '--';
        }

        const res1h = await fetch(`/api/telemetry/rollup/1h?truck_id=${activeTruck}`);
        const data1h = await res1h.json();
        if (data1h && data1h.avg_speed_kmh !== undefined) {
            document.getElementById('rollup-1h-speed').textContent = Math.round(data1h.avg_speed_kmh);
            document.getElementById('rollup-1h-brakes').textContent = data1h.harsh_brake_count || 0;
        } else {
            document.getElementById('rollup-1h-speed').textContent = '--';
            document.getElementById('rollup-1h-brakes').textContent = '--';
        }
    } catch (e) {
        console.error('Failed to fetch rollups', e);
    }
}

function startFetchingRollups() {
    if (rollupInterval) clearInterval(rollupInterval);
    fetchRollups();
    rollupInterval = setInterval(fetchRollups, 5000);
}

// ─── LOGIN ───
document.getElementById('login-btn').addEventListener('click', () => {
    const user = document.getElementById('login-user').value;
    const pass = document.getElementById('login-pass').value;
    const err = document.getElementById('login-error');

    if (CREDENTIALS[user] && CREDENTIALS[user].pass === pass) {
        err.classList.remove('visible');
        currentUserRole = CREDENTIALS[user].role;

        if (currentUserRole === 'driver') {
            activeTruck = CREDENTIALS[user].truck;
            // For driver view, hide map panel and show only right panel full width
            document.querySelector('.main-grid').style.gridTemplateColumns = '1fr';
            document.querySelector('.map-panel').style.display = 'none';
        } else {
            activeTruck = 'TRK-001';
        }

        // Transition
        const overlay = document.getElementById('login-overlay');
        overlay.style.opacity = '0';
        setTimeout(() => overlay.style.display = 'none', 500);

        const app = document.getElementById('app-container');
        app.classList.add('visible');

        initMap();
        initChart();
        connectMQTT();
        startFetchingAlerts();
        startFetchingRollups();
    } else {
        err.classList.add('visible');
    }
});

// ─── INIT ───
window.addEventListener('DOMContentLoaded', () => {
    // Wait for login
});
