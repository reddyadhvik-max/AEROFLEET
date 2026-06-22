// ═══════════════════════════════════════════
//  AEROFLEET — ANOMALY INJECTOR
// ═══════════════════════════════════════════

const SLIDERS = [
    { id: 'speed_kmh', label: 'Speed (km/h)', min: 0, max: 140, step: 1 },
    { id: 'rpm', label: 'Engine Speed (RPM)', min: 500, max: 2500, step: 10 },
    { id: 'fuel_pct', label: 'Fuel Level (%)', min: 0, max: 100, step: 0.5 },
    { id: 'brake_g', label: 'Brake Force (G)', min: 0, max: 1.0, step: 0.05 },
    { id: 'tyre_psi', label: 'Tyre Pressure (PSI)', min: 50, max: 120, step: 1 },
    { id: 'coolant_temp_f', label: 'Coolant Temp (°F)', min: 100, max: 260, step: 1 },
    { id: 'oil_pressure_psi', label: 'Oil Pressure (PSI)', min: 10, max: 80, step: 1 },
    { id: 'boost_pressure_psi', label: 'Boost Pressure (PSI)', min: 0, max: 50, step: 1 }
];

let mqttClient = null;

function initUI() {
    const container = document.getElementById('sliders-container');

    SLIDERS.forEach(s => {
        const div = document.createElement('div');
        div.className = 'slider-item';
        div.innerHTML = `
            <div class="slider-header">
                <span class="slider-label">${s.label}</span>
                <span id="val-${s.id}" class="slider-value auto">Auto</span>
            </div>
            <div class="slider-row">
                <input type="range" id="slider-${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.min}" disabled>
                <input type="checkbox" id="enable-${s.id}">
            </div>
        `;
        container.appendChild(div);

        const slider = document.getElementById(`slider-${s.id}`);
        const enable = document.getElementById(`enable-${s.id}`);
        const valDisplay = document.getElementById(`val-${s.id}`);

        enable.addEventListener('change', (e) => {
            slider.disabled = !e.target.checked;
            if (e.target.checked) {
                valDisplay.textContent = slider.value;
                valDisplay.classList.remove('auto');
                valDisplay.classList.add('override');
            } else {
                valDisplay.textContent = 'Auto (' + Number(slider.value).toFixed(1) + ')';
                valDisplay.classList.remove('override');
                valDisplay.classList.add('auto');
            }
        });

        slider.addEventListener('input', (e) => {
            valDisplay.textContent = e.target.value;
        });
    });
}

function connectMQTT() {
    const host = window.location.hostname || 'localhost';
    const brokerUrl = `ws://${host}:8083/mqtt`;
    mqttClient = mqtt.connect(brokerUrl);

    mqttClient.on('connect', () => {
        console.log('MQTT Connected (Injector)');
        mqttClient.subscribe('truck/+/telemetry');
    });

    mqttClient.on('message', (topic, message) => {
        try {
            if (topic.endsWith('/telemetry')) {
                const data = JSON.parse(message.toString());
                const targetTruck = document.getElementById('target-truck').value;
                if (data.truck_id === targetTruck) {
                    SLIDERS.forEach(s => {
                        const enable = document.getElementById(`enable-${s.id}`);
                        if (!enable.checked) {
                            let val = data[s.id];
                            if (val !== undefined) {
                                const slider = document.getElementById(`slider-${s.id}`);
                                const valDisplay = document.getElementById(`val-${s.id}`);
                                slider.value = val;
                                valDisplay.textContent = 'Auto (' + Number(val).toFixed(1) + ')';
                            }
                        }
                    });
                }
            }
        } catch (e) {
            console.error('MQTT parsing error', e);
        }
    });
}

// Apply Overrides
document.getElementById('apply-overrides').addEventListener('click', () => {
    if (!mqttClient || !mqttClient.connected) return alert('MQTT not connected!');

    const targetTruck = document.getElementById('target-truck').value;
    const overrides = {};

    SLIDERS.forEach(s => {
        const enabled = document.getElementById(`enable-${s.id}`).checked;
        if (enabled) {
            overrides[s.id] = document.getElementById(`slider-${s.id}`).value;
        } else {
            overrides[s.id] = '';  // Clear override
        }
    });

    mqttClient.publish(`truck/${targetTruck}/command/override`, JSON.stringify(overrides));

    // Show success popup
    const popup = document.getElementById('success-popup');
    popup.classList.add('visible');
    setTimeout(() => {
        popup.classList.remove('visible');
    }, 3000);
});

// Clear Overrides
document.getElementById('clear-overrides').addEventListener('click', () => {
    SLIDERS.forEach(s => {
        document.getElementById(`enable-${s.id}`).checked = false;
        document.getElementById(`slider-${s.id}`).disabled = true;

        const valDisplay = document.getElementById(`val-${s.id}`);
        valDisplay.textContent = 'Auto';
        valDisplay.classList.remove('override');
        valDisplay.classList.add('auto');
    });

    if (mqttClient && mqttClient.connected) {
        const targetTruck = document.getElementById('target-truck').value;
        const overrides = {};
        SLIDERS.forEach(s => overrides[s.id] = '');
        mqttClient.publish(`truck/${targetTruck}/command/override`, JSON.stringify(overrides));
    }
});

// Force Alert
document.getElementById('force-alert-btn').addEventListener('click', async () => {
    const targetTruck = document.getElementById('target-truck').value;
    const [severity, type] = document.getElementById('alert-type-selector').value.split('|');
    const statusEl = document.getElementById('alert-status');

    statusEl.textContent = 'Triggering...';
    statusEl.style.color = '#FFB800';

    try {
        await fetch('/api/alerts/emergency', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                truck_id: targetTruck,
                type: type,
                severity: severity,
                description: `Manual Alert: ${type} injected by Admin testing.`
            })
        });
        statusEl.textContent = 'Alert triggered!';
        statusEl.style.color = '#00FF88';
        setTimeout(() => { statusEl.textContent = ''; }, 3000);
    } catch (e) {
        statusEl.textContent = 'Failed';
        statusEl.style.color = '#FF2D2D';
    }
});

// Init
window.addEventListener('DOMContentLoaded', () => {
    initUI();
    connectMQTT();
});
