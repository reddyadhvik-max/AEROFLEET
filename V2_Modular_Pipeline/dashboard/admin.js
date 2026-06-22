// ==========================================
// AEROFLEET V2 - ADMIN PORTAL
// ==========================================

const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
if (!token) {
    window.location.href = 'index.html';
}
// No access token needed for MapLibre with CARTO tiles

let map;
let markers = {};
let allAlerts = [];
let currentEventId = null;

function initMap() {
    map = new maplibregl.Map({
        container: 'fleet-map',
        style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', // Open-source dark theme
        center: [0, 0], // Default center
        zoom: 2
    });

    map.on('load', () => {
        console.log("Mapbox loaded");
        fetchFleetLocations();
        setInterval(fetchFleetLocations, 2000); // Poll every 2s
    });
}

function switchTab(tabId, element) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    element.classList.add('active');
    
    if (tabId === 'enroll-tab') {
        if(typeof startAdminCamera === 'function') startAdminCamera();
    } else {
        if(typeof stopAdminCamera === 'function') stopAdminCamera();
    }
    
    // Mapbox needs a resize event if it was hidden when initialized
    if (tabId === 'map-tab' && map) {
        setTimeout(() => map.resize(), 100);
    }
    
    if (tabId === 'analytics-tab') {
        loadAnalytics();
    }
}

// ------------------------------------------
// FLEET TRACKING & MAP
// ------------------------------------------
async function fetchFleetLocations() {
    try {
        const res = await fetch('/api/fleet/locations');
        const fleetData = await res.json();
        
        // Also fetch status to see if TRK-001 has local simulated data
        const statRes = await fetch('/api/status');
        const statData = await statRes.json();
        
        let localTruckId = statData.truck_id || "TRK-001";
        
        // Ensure the central truck exists in our data if simulator is active
        if (!fleetData[localTruckId]) {
            // Simulated dummy location if MQTT is offline
            fleetData[localTruckId] = {
                lat: 40.7128, lng: -74.0060, speed: 65, active: true
            };
        }
        
        updateMapMarkers(fleetData);
        
        const selectedTruck = document.getElementById('truck-selector').value;
        if (selectedTruck) {
            updateSpecificTruckDetails(selectedTruck, statData, fleetData);
        } else {
            updateSpecificTruckDetails(localTruckId, statData, fleetData);
        }
        
        localStorage.setItem('cached_fleet', JSON.stringify(fleetData));
        localStorage.setItem('cached_stat', JSON.stringify(statData));
    } catch (e) {
        console.log("Failed to fetch fleet locations:", e);
        const cachedFleet = localStorage.getItem('cached_fleet');
        const cachedStat = localStorage.getItem('cached_stat');
        if (cachedFleet && cachedStat) {
            const fleetData = JSON.parse(cachedFleet);
            const statData = JSON.parse(cachedStat);
            updateMapMarkers(fleetData);
            const selectedTruck = document.getElementById('truck-selector').value;
            if (selectedTruck) {
                updateSpecificTruckDetails(selectedTruck, statData, fleetData);
            }
        }
    }
}

function updateMapMarkers(fleetData) {
    let bounds = new maplibregl.LngLatBounds();
    let hasMarkers = false;
    
    for (const [truckId, data] of Object.entries(fleetData)) {
        if (!data.lat || !data.lng) continue;
        
        if (!markers[truckId]) {
            const el = document.createElement('div');
            el.className = 'marker';
            
            // Clicking marker opens Truck Details tab
            el.addEventListener('click', () => {
                document.getElementById('truck-selector').value = truckId;
                loadTruckDetails(truckId);
                switchTab('details-tab', document.querySelectorAll('.nav-item')[3]);
            });

            markers[truckId] = new maplibregl.Marker(el)
                .setLngLat([data.lng, data.lat])
                .setPopup(new maplibregl.Popup({ offset: 25 })
                .setHTML(`<strong>${truckId}</strong><br>Speed: ${data.speed} km/h`))
                .addTo(map);
        } else {
            markers[truckId].setLngLat([data.lng, data.lat]);
            markers[truckId].getPopup().setHTML(`<strong>${truckId}</strong><br>Speed: ${data.speed || 0} km/h<br>Fuel: ${Math.round(data.fuel || 100)}%`);
        }
        
        // Update route polyline
        if (data.route && data.route.length > 0) {
            const routeId = `route-${truckId}`;
            if (map.getSource(routeId)) {
                map.getSource(routeId).setData({
                    type: 'Feature',
                    geometry: { type: 'LineString', coordinates: data.route }
                });
            } else {
                map.addSource(routeId, {
                    type: 'geojson',
                    data: {
                        type: 'Feature',
                        geometry: { type: 'LineString', coordinates: data.route }
                    }
                });
                map.addLayer({
                    id: routeId,
                    type: 'line',
                    source: routeId,
                    layout: { 'line-join': 'round', 'line-cap': 'round' },
                    paint: { 'line-color': '#00ffcc', 'line-width': 3, 'line-opacity': 0.6 }
                });
            }
        }
        
        bounds.extend([data.lng, data.lat]);
        hasMarkers = true;
    }
}

// ------------------------------------------
// ALERTS & VIDEO CLIPS
// ------------------------------------------
async function fetchAlerts() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/alerts', {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        allAlerts = await res.json();
        localStorage.setItem('cached_alerts', JSON.stringify(allAlerts));
        renderGlobalAlerts();
    } catch(e) {
        const cached = localStorage.getItem('cached_alerts');
        if (cached) {
            allAlerts = JSON.parse(cached);
            renderGlobalAlerts();
        }
    }
}

function renderGlobalAlerts() {
    const tbody = document.querySelector("#global-alerts-table tbody");
    tbody.innerHTML = '';
    
    allAlerts.forEach(alert => {
        const tr = document.createElement('tr');
        
        let sevClass = 'severity-low';
        if(alert.severity === 'CRITICAL') sevClass = 'severity-critical';
        if(alert.severity === 'HIGH') sevClass = 'severity-high';
        
        tr.innerHTML = `
            <td>${new Date(alert.time).toLocaleTimeString()}</td>
            <td>${alert.truck_id || 'N/A'}</td>
            <td>${alert.driver_name || 'Unknown'}</td>
            <td>${alert.type}</td>
            <td><span class="severity-badge ${sevClass}">${alert.severity}</span></td>
            <td><button class="btn" style="background:var(--primary);color:white;border:none;padding:5px 10px;border-radius:4px;cursor:pointer;" onclick="viewVideo('${alert.event_id || alert.id}', '${alert.type}')">View Clip</button></td>
        `;
        tbody.appendChild(tr);
    });
}

function viewVideo(eventId, alertType) {
    // Note: older alerts might not have event_id if generated before this update.
    // In that case, we can't fetch video. 
    if (typeof eventId === 'number' || !eventId.includes('_')) {
        alert("Video clip not available for legacy alerts.");
        return;
    }
    
    currentEventId = eventId;
    document.getElementById('video-title').innerText = `Event Viewer: ${alertType}`;
    document.getElementById('video-status').innerText = '';
    document.getElementById('video-modal').style.display = 'flex';
    
    const player = document.getElementById('alert-video-player');
    // Try to load the 20s short clip immediately
    player.src = `/api/alerts/${eventId}/video/20s`;
    player.play().catch(e => {
        document.getElementById('video-status').innerText = 'Video clip is still being processed or unavailable.';
    });
}

document.getElementById('btn-fetch-60s').addEventListener('click', () => {
    if (!currentEventId) return;
    document.getElementById('video-status').innerText = 'Requesting 60s clip from cloud...';
    
    const player = document.getElementById('alert-video-player');
    // Load 60s clip endpoint (will fall back to local if cloud not setup)
    player.src = `/api/alerts/${currentEventId}/video/60s`;
    player.play().then(() => {
        document.getElementById('video-status').innerText = 'Loaded 60s clip.';
    }).catch(e => {
        document.getElementById('video-status').innerText = '60s clip unavailable.';
    });
});

function closeVideoModal() {
    document.getElementById('video-modal').style.display = 'none';
    document.getElementById('alert-video-player').pause();
    document.getElementById('alert-video-player').src = '';
    currentEventId = null;
}

// ------------------------------------------
// SPECIFIC TRUCK DETAILS
// ------------------------------------------
async function loadAdminTrucks() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/trucks', {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        
        if (!res.ok) {
            if (res.status === 401) window.location.href = 'index.html';
            throw new Error(`HTTP error! status: ${res.status}`);
        }
        
        const trucks = await res.json();
        
        const select = document.getElementById('truck-selector');
        const enrollSelect = document.getElementById('enroll-truck-id');
        const currentVal = select.value;
        const enrollVal = enrollSelect.value;
        
        select.innerHTML = '<option value="">Select a Truck...</option>';
        enrollSelect.innerHTML = '<option value="">None</option>';
        
        trucks.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.innerText = t;
            select.appendChild(opt);
            
            const opt2 = document.createElement('option');
            opt2.value = t;
            opt2.innerText = t;
            enrollSelect.appendChild(opt2);
        });
        
        if (trucks.includes(currentVal)) select.value = currentVal;
        if (trucks.includes(enrollVal)) enrollSelect.value = enrollVal;
    } catch(e) {
        console.error("Failed to load trucks", e);
    }
}

async function addNewTruck() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/trucks', {
            method: 'POST',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if (res.ok) {
            const data = await res.json();
            alert("Created new truck: " + data.truck_id);
            await loadAdminTrucks();
            document.getElementById('truck-selector').value = data.truck_id;
            loadTruckDetails(data.truck_id);
        } else {
            alert("Failed to create truck");
        }
    } catch(e) {
        console.error(e);
    }
}

function loadTruckDetails(truckId) {
    if (!truckId) {
        document.getElementById('truck-details-content').style.display = 'none';
        return;
    }
    document.getElementById('truck-details-content').style.display = 'block';
    renderSpecificAlerts(truckId);
}

function updateSpecificTruckDetails(truckId, statData, fleetData) {
    if (document.getElementById('truck-selector').value !== truckId) return;
    
    const data = fleetData && fleetData[truckId] ? fleetData[truckId] : null;
    
    document.getElementById('spec-driver').innerText = (data && data.driver_name) ? data.driver_name : 'Unknown';
    
    if (data) {
        document.getElementById('spec-speed').innerText = `${data.speed || 0} km/h`;
        document.getElementById('spec-rpm').innerText = data.rpm || 0;
        document.getElementById('spec-fuel').innerText = `${Math.round(data.fuel || 100)}%`;
        
        if (data.route && data.route.length > 0) {
            document.getElementById('spec-route').innerText = `Active Route (${data.route.length} waypoints)`;
            document.getElementById('spec-route').style.color = '#00ffcc';
        } else {
            document.getElementById('spec-route').innerText = 'No active route set';
            document.getElementById('spec-route').style.color = 'var(--text-muted)';
        }
    }
}

function renderSpecificAlerts(truckId) {
    const tbody = document.querySelector("#spec-truck-alerts tbody");
    tbody.innerHTML = '';
    
    const filtered = allAlerts.filter(a => a.truck_id === truckId);
    
    filtered.forEach(alert => {
        const tr = document.createElement('tr');
        
        let sevClass = 'severity-low';
        if(alert.severity === 'CRITICAL') sevClass = 'severity-critical';
        if(alert.severity === 'HIGH') sevClass = 'severity-high';
        
        tr.innerHTML = `
            <td>${new Date(alert.time).toLocaleTimeString()}</td>
            <td>${alert.type}</td>
            <td><span class="severity-badge ${sevClass}">${alert.severity}</span></td>
            <td><button class="btn" style="background:var(--primary);color:white;border:none;padding:5px 10px;border-radius:4px;cursor:pointer;" onclick="viewVideo('${alert.event_id || alert.id}', '${alert.type}')">View Clip</button></td>
        `;
        tbody.appendChild(tr);
    });
}

// ------------------------------------------
// ENROLLMENT (from previous implementation)
// ------------------------------------------
// Code remains similar... 
async function loadEnrolledDrivers() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch(`/api/drivers?_t=${Date.now()}`, {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        const drivers = await res.json();
        const tbody = document.querySelector("#enrolled-drivers-table tbody");
        tbody.innerHTML = '';
        
        const truckOptions = Array.from(document.getElementById('truck-selector').options)
            .filter(opt => opt.value !== '')
            .map(opt => `<option value="${opt.value}">${opt.value}</option>`)
            .join('');

        drivers.forEach(d => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><img src="/api/drivers/${d.id}/thumbnail" style="width:40px; height:40px; border-radius:50%; object-fit:cover;"></td>
                <td>${d.id}</td>
                <td>${d.name}</td>
                <td>
                    <select id="reassign-truck-${d.id}" style="padding:4px; background:rgba(0,0,0,0.5); color:white; border:1px solid rgba(255,255,255,0.2); border-radius:4px;">
                        <option value="">None</option>
                        ${truckOptions}
                    </select>
                </td>
                <td>
                    <button class="btn" style="background:var(--primary);color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;margin-right:5px;" onclick="reassignDriver('${d.id}')">Assign</button>
                    <button class="btn" style="background:rgba(239,68,68,0.2);color:#ef4444;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;" onclick="deleteDriver('${d.id}')">Remove</button>
                </td>
            `;
            tbody.appendChild(tr);
            if (d.assigned_truck) {
                document.getElementById(`reassign-truck-${d.id}`).value = d.assigned_truck;
            }
        });
    } catch(e) {}
}

async function reassignDriver(driverId) {
    const newTruck = document.getElementById(`reassign-truck-${driverId}`).value;
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch(`/api/drivers/${driverId}/reassign`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {})
            },
            body: JSON.stringify({ assigned_truck: newTruck })
        });
        if (res.ok) {
            alert(`Driver reassigned to ${newTruck || 'None'}`);
            loadEnrolledDrivers();
        } else {
            alert("Failed to reassign driver");
        }
    } catch(e) {
        alert("Server error");
    }
}

async function enrollDriverCapture() {
    const id = document.getElementById('new-driver-id').value;
    const name = document.getElementById('new-driver-name').value;
    const password = document.getElementById('new-driver-password').value;
    const assigned_truck = document.getElementById('enroll-truck-id').value;
    if(!id || !name || !password) return alert("Enter ID, Name, and Password");
    if(!capturedBase64) return alert("Please capture a photo first!");
    
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const headers = {'Content-Type': 'application/json'};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        
        const res = await fetch('/api/drivers/enroll/camera', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ driver_id: id, name: name, password: password, image: capturedBase64, assigned_truck: assigned_truck })
        });
        const data = await res.json();
        if(res.ok) {
            alert("Driver enrolled successfully!");
            loadEnrolledDrivers();
            retakePhoto(); // reset UI
            document.getElementById('new-driver-id').value = '';
            document.getElementById('new-driver-name').value = '';
            document.getElementById('new-driver-password').value = '';
        } else {
            alert("Error: " + data.detail);
        }
    } catch(e) {
        alert("Failed to reach server");
    }
}

async function enrollDriverUpload() {
    const id = document.getElementById('new-driver-id').value;
    const name = document.getElementById('new-driver-name').value;
    const password = document.getElementById('new-driver-password').value;
    const file = document.getElementById('upload-photo').files[0];
    const assigned_truck = document.getElementById('enroll-truck-id').value;
    
    if(!id || !name || !password || !file) return alert("Enter ID, Name, Password, and select a file");
    
    const formData = new FormData();
    formData.append('driver_id', id);
    formData.append('name', name);
    formData.append('password', password);
    formData.append('file', file);
    if (assigned_truck) formData.append('assigned_truck', assigned_truck);
    
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const headers = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        
        const res = await fetch('/api/drivers/enroll/upload', {
            method: 'POST',
            headers: headers,
            body: formData
        });
        const data = await res.json();
        if(res.ok) {
            alert("Driver uploaded & enrolled successfully!");
            loadEnrolledDrivers();
            document.getElementById('new-driver-id').value = '';
            document.getElementById('new-driver-name').value = '';
            document.getElementById('new-driver-password').value = '';
            document.getElementById('upload-photo').value = '';
        } else {
            alert("Error: " + data.detail);
        }
    } catch(e) {
        alert("Failed to reach server");
    }
}

async function deleteDriver(id) {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const headers = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        
        const res = await fetch(`/api/drivers/${id}`, {
            method: 'DELETE',
            headers: headers
        });
        if (res.ok) {
            loadEnrolledDrivers();
        } else {
            console.error("Failed to delete driver");
        }
    } catch(e) {
        console.error("Error deleting driver:", e);
    }
}


// Initialization
setInterval(() => {
    document.getElementById('sys-time').innerText = new Date().toLocaleTimeString();
}, 1000);

setInterval(fetchAlerts, 2000);

let riskChartInstance = null;

async function loadAnalytics() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        
        // 1. Fetch Summary
        const sumRes = await fetch('/api/analytics/summary', { headers });
        if (!sumRes.ok) {
            if (sumRes.status === 401) window.location.href = 'index.html';
            throw new Error(`HTTP error! status: ${sumRes.status}`);
        }
        const summary = await sumRes.json();
        
        document.getElementById('total-distance-val').innerText = summary.total_distance_km || 0;
        document.getElementById('total-fuel-val').innerText = summary.total_fuel_consumed;
        document.getElementById('fleet-score-val').innerText = summary.fleet_safety_score;
        
        // 2. Fetch Drivers
        const drvRes = await fetch('/api/analytics/drivers', { headers });
        if (!drvRes.ok) throw new Error(`HTTP error! status: ${drvRes.status}`);
        const drivers = await drvRes.json();
        
        const labels = drivers.map(d => d.name);
        const data = drivers.map(d => d.risk_score);
        
        const ctx = document.getElementById('riskChart').getContext('2d');
        if (riskChartInstance) riskChartInstance.destroy();
        
        riskChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Risk Score (Alerts per 100km)',
                    data: data,
                    backgroundColor: 'rgba(239, 68, 68, 0.7)',
                    borderColor: 'rgb(239, 68, 68)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: { beginAtZero: true }
                }
            }
        });
        
    } catch(e) {
        console.error("Failed to load analytics", e);
    }
}

// init map etc
initMap();
fetchAlerts();

(async () => {
    await loadAdminTrucks();
    await loadEnrolledDrivers();
})();

async function systemShutdown() {
    if(!confirm("Are you sure you want to completely shut down the AEROFLEET system?")) return;
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/system/shutdown', {
            method: 'POST',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if(res.ok) {
            alert("System shutting down...");
            window.close();
        }
    } catch(e) {
        alert("Shutdown command sent.");
    }
}

function resetVerification() {
    const truckId = document.getElementById('truck-selector').value;
    if (!truckId) return alert("Please select a truck from the dropdown first.");
    
    const password = prompt(`Enter Admin Password to reset verification for ${truckId}:`);
    if (!password) return;
    
    const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
    fetch(`/api/trucks/${truckId}/reset_verification`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ password: password })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "ok") {
            alert("Verification checks reset successfully.");
        } else {
            alert("Reset failed: " + data.detail);
        }
    })
    .catch(err => {
        console.error(err);
        alert("An error occurred while resetting.");
    });
}

let adminVideoStream = null;
let capturedBase64 = null;

function startAdminCamera() {
    const video = document.getElementById('admin-webcam');
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        navigator.mediaDevices.getUserMedia({ video: true }).then(function(stream) {
            adminVideoStream = stream;
            video.srcObject = stream;
            video.play();
        }).catch(function(err) {
            console.error('Camera access denied or unavailable.', err);
            if (err.name === "NotReadableError") {
                alert("The camera is currently locked by the vehicle backend! Please use the 'Upload Photo' option to enroll drivers while testing on a single PC.");
            } else {
                alert("Camera access denied or unavailable: " + err.message);
            }
        });
    } else {
        alert('Webcam API not supported in this browser.');
    }
}

function stopAdminCamera() {
    if (adminVideoStream) {
        adminVideoStream.getTracks().forEach(track => track.stop());
        adminVideoStream = null;
    }
}

function takeSnapshot() {
    const video = document.getElementById('admin-webcam');
    const canvas = document.getElementById('admin-canvas');
    const img = document.getElementById('admin-snapshot');
    
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    capturedBase64 = canvas.toDataURL('image/jpeg', 0.9);
    
    img.src = capturedBase64;
    video.style.display = 'none';
    img.style.display = 'block';
    
    document.getElementById('btn-capture').style.display = 'none';
    document.getElementById('btn-retake').style.display = 'block';
    document.getElementById('btn-register').style.display = 'block';
}

function retakePhoto() {
    capturedBase64 = null;
    document.getElementById('admin-webcam').style.display = 'block';
    document.getElementById('admin-snapshot').style.display = 'none';
    
    document.getElementById('btn-capture').style.display = 'block';
    document.getElementById('btn-retake').style.display = 'none';
    document.getElementById('btn-register').style.display = 'none';
}


async function stealCamera() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/system/camera/pause', {
            method: 'POST',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if(res.ok) {
            document.getElementById('btn-steal-camera').style.display = 'none';
            document.getElementById('btn-return-camera').style.display = 'inline-block';
            startAdminCamera(); 
        } else {
            alert('Server rejected the pause request');
        }
    } catch(e) {
        alert('Failed to pause backend camera');
    }
}

async function returnCamera() {
    try {
        const token = (localStorage.getItem('admin_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/system/camera/resume', {
            method: 'POST',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if(res.ok) {
            document.getElementById('btn-steal-camera').style.display = 'inline-block';
            document.getElementById('btn-return-camera').style.display = 'none';
            stopAdminCamera();
            alert('Backend camera resumed.');
        } else {
            alert('Server rejected the resume request');
        }
    } catch(e) {
        alert('Failed to resume backend camera');
    }
}

