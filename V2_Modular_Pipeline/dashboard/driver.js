// ==========================================
// AEROFLEET V2 - DRIVER PORTAL
// ==========================================

// No access token needed for MapLibre with CARTO tiles

let driverMap;
let routeMarker;

function switchTab(tabId, element) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    element.classList.add('active');
    
    if (tabId === 'nav-tab' && driverMap) {
        setTimeout(() => driverMap.resize(), 100);
    }
}

function initMap() {
    driverMap = new maplibregl.Map({
        container: 'driver-map',
        style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', // Open-source dark theme
        center: [-98.5795, 39.8283], // US center
        zoom: 3
    });

    driverMap.on('load', () => {
        // Add a marker for the current truck
        const el = document.createElement('div');
        el.className = 'marker';
        el.style.backgroundImage = "url('https://cdn-icons-png.flaticon.com/512/3204/3204044.png')";
        el.style.width = '30px';
        el.style.height = '30px';
        el.style.backgroundSize = 'cover';
        
        routeMarker = new maplibregl.Marker(el)
            .setLngLat([-98.5795, 39.8283])
            .addTo(driverMap);
            
        fetchTelemetry();
        setInterval(fetchTelemetry, 2000);
    });
}

async function geocode(query) {
    const res = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1`);
    const data = await res.json();
    if (data && data.length > 0) {
        return [parseFloat(data[0].lon), parseFloat(data[0].lat)];
    }
    return null;
}

async function calculateRoute() {
    const start = document.getElementById('start-loc').value;
    const end = document.getElementById('end-loc').value;
    if (!start || !end) return alert("Enter both start and end locations.");
    
    try {
        const startCoords = await geocode(start);
        const endCoords = await geocode(end);
        
        if (!startCoords || !endCoords) {
            return alert("Could not find coordinates for the provided locations.");
        }
        
        const res = await fetch(`https://router.project-osrm.org/route/v1/driving/${startCoords[0]},${startCoords[1]};${endCoords[0]},${endCoords[1]}?overview=full&geometries=geojson`);
        const data = await res.json();
        
        if (data.routes && data.routes.length > 0) {
            const route = data.routes[0].geometry;
            totalRouteDistanceKm = data.routes[0].distance / 1000;
            
            document.getElementById('sim-dist').innerText = `${totalRouteDistanceKm.toFixed(1)} km`;
            
            if (driverMap.getSource('route')) {
                driverMap.getSource('route').setData(route);
            } else {
                driverMap.addSource('route', {
                    'type': 'geojson',
                    'data': route
                });
                driverMap.addLayer({
                    'id': 'route',
                    'type': 'line',
                    'source': 'route',
                    'layout': {
                        'line-join': 'round',
                        'line-cap': 'round'
                    },
                    'paint': {
                        'line-color': '#00ffcc',
                        'line-width': 5,
                        'line-opacity': 0.8
                    }
                });
            }
            
            // Fit map to route bounds
            const coordinates = route.coordinates;
            const bounds = coordinates.reduce((bounds, coord) => {
                return bounds.extend(coord);
            }, new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
            
            driverMap.fitBounds(bounds, {
                padding: 50
            });
            
            // Reveal start button
            document.getElementById('start-journey-btn').style.display = 'inline-block';
            document.getElementById('start-journey-btn').innerText = 'Start Journey';
            document.getElementById('halt-journey-btn').style.display = 'none';
            document.getElementById('end-journey-btn').style.display = 'none';
        } else {
            alert("Could not calculate a route.");
        }
    } catch (error) {
        console.error("Routing error:", error);
        alert("An error occurred while calculating the route.");
    }
}

// ------------------------------------------
// TELEMETRY & ALERTS
// ------------------------------------------
let routeCoords = [];
let totalRouteDistanceKm = 0;

function startJourney() {
    if (!driverMap.getSource('route')) return alert("Please set a route first!");
    const data = driverMap.getSource('route')._data;
    if (!data || !data.coordinates) return alert("Route data not found!");
    
    if (routeCoords.length === 0) {
        routeCoords = data.coordinates;
    }
    
    document.getElementById('start-journey-btn').style.display = 'none';
    document.getElementById('halt-journey-btn').style.display = 'inline-block';
    document.getElementById('end-journey-btn').style.display = 'inline-block';
    
    const truckId = localStorage.getItem("truck_id") || "TRK-001";
    const token = (localStorage.getItem('driver_access_token') || localStorage.getItem('access_token'));
    
    fetch('/api/fleet/start-journey', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({
            truck_id: truckId,
            route_coords: routeCoords
        })
    }).then(() => {
        console.log("Journey started on server.");
    }).catch(e => console.error(e));
}

function haltJourney() {
    endJourney();
}

function endJourney() {
    routeCoords = [];
    
    document.getElementById('start-journey-btn').innerText = "Start Journey";
    document.getElementById('start-journey-btn').style.display = 'inline-block';
    document.getElementById('halt-journey-btn').style.display = 'none';
    document.getElementById('end-journey-btn').style.display = 'none';
    document.getElementById('sim-dist').innerText = "0 km";
    
    const truckId = localStorage.getItem("truck_id") || "TRK-001";
    const token = (localStorage.getItem('driver_access_token') || localStorage.getItem('access_token'));
    
    fetch('/api/fleet/end-journey', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({ truck_id: truckId })
    });
    
    alert("Journey Ended.");
}

async function fetchTelemetry() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Update driver info
        const driverName = localStorage.getItem('driver_name') || 'Unknown Driver';
        const myTruckId = localStorage.getItem("truck_id") || "TRK-001";
        
        document.getElementById('current-driver-name').innerText = driverName;
        document.getElementById('current-truck-id').innerText = `Truck: ${myTruckId}`;
        
        // Let's also fetch fleet locations to get our simulated GPS
        const locRes = await fetch('/api/fleet/locations');
        const locData = await locRes.json();
        
        localStorage.setItem('cached_driver_stat', JSON.stringify(data));
        localStorage.setItem('cached_driver_loc', JSON.stringify(locData));
        
        updateTelemetryUI(locData, myTruckId);
        if (data && data.pipeline) {
            updateVerificationUI(data.pipeline);
        }
    } catch(e) {
        const cachedLoc = localStorage.getItem('cached_driver_loc');
        if (cachedLoc) {
            const myTruckId = localStorage.getItem("truck_id") || "TRK-001";
            updateTelemetryUI(JSON.parse(cachedLoc), myTruckId);
        }
        const cachedStat = localStorage.getItem('cached_driver_stat');
        if (cachedStat) {
            const parsedStat = JSON.parse(cachedStat);
            if (parsedStat && parsedStat.pipeline) updateVerificationUI(parsedStat.pipeline);
        }
    }
}

function updateTelemetryUI(locData, myTruckId) {
    const myData = locData[myTruckId];
    if (myData) {
        document.getElementById('sim-speed').innerText = `${myData.speed || 0} km/h`;
        document.getElementById('sim-rpm').innerText = myData.rpm || 0;
        document.getElementById('sim-fuel').innerText = `${Math.round(myData.fuel) || 100}%`;
        
        if (myData.lat && myData.lng && routeMarker) {
            routeMarker.setLngLat([myData.lng, myData.lat]);
            driverMap.panTo([myData.lng, myData.lat], {duration: 1000});
        }
    }
}

function updateVerificationUI(pipeline) {
    const badgeText = document.getElementById('driver-status-text');
    const badgeDot = document.getElementById('driver-status-dot');
    const body = document.body;

    // Remove existing hue classes
    body.classList.remove('hue-verified-green', 'hue-warn-yellow', 'hue-warn-orange', 'hue-alert-red');

    const driverName = pipeline.current_driver_name || "Unknown";

    if (pipeline.missed_checks === 0 && driverName !== "Unverified") {
        body.classList.add('hue-verified-green');
        badgeText.innerText = `Valid Driver: ${driverName}`;
        badgeText.style.color = "#32d74b";
        badgeDot.style.backgroundColor = "#32d74b";
    } else {
        const chancesLeft = Math.max(0, 4 - pipeline.missed_checks);
        
        if (pipeline.missed_checks === 1) {
            body.classList.add('hue-warn-yellow');
            badgeText.innerText = `Unverified Driver (${chancesLeft} chances left)`;
            badgeText.style.color = "#ffcc00";
            badgeDot.style.backgroundColor = "#ffcc00";
        } else if (pipeline.missed_checks === 2) {
            body.classList.add('hue-warn-orange');
            badgeText.innerText = `Unverified Driver (${chancesLeft} chances left)`;
            badgeText.style.color = "#ff9500";
            badgeDot.style.backgroundColor = "#ff9500";
        } else {
            body.classList.add('hue-alert-red');
            badgeText.innerText = chancesLeft > 0 ? `Unverified Driver (${chancesLeft} chance left)` : `Unverified Driver (Alert Sent!)`;
            badgeText.style.color = "#ff3b30";
            badgeDot.style.backgroundColor = "#ff3b30";
        }
    }
}

async function fetchAlerts() {
    try {
        const token = (localStorage.getItem('driver_access_token') || localStorage.getItem('access_token'));
        const res = await fetch('/api/alerts', {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        const alerts = await res.json();
        localStorage.setItem('cached_driver_alerts', JSON.stringify(alerts));
        renderDriverAlerts(alerts);
    } catch(e) {
        const cached = localStorage.getItem('cached_driver_alerts');
        if (cached) {
            renderDriverAlerts(JSON.parse(cached));
        }
    }
}

function renderDriverAlerts(alerts) {
    const tbody = document.querySelector("#driver-alerts tbody");
    tbody.innerHTML = '';
    
    // Only show alerts for this truck, but for now we assume all alerts in this backend instance belong to it.
    alerts.forEach(alert => {
        const tr = document.createElement('tr');
        
        let sevClass = 'severity-low';
        if(alert.severity === 'CRITICAL') sevClass = 'severity-critical';
        if(alert.severity === 'HIGH') sevClass = 'severity-high';
        
        tr.innerHTML = `
            <td>${new Date(alert.time).toLocaleTimeString()}</td>
            <td>${alert.type}</td>
            <td><span class="severity-badge ${sevClass}">${alert.severity}</span></td>
            <td>${alert.description}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Initialization
setInterval(() => {
    document.getElementById('sys-time').innerText = new Date().toLocaleTimeString();
}, 1000);

setInterval(fetchAlerts, 2000);

initMap();
fetchAlerts();

function logout() {
    localStorage.clear();
    window.location.href = "/index.html";
}


async function forceVerify() {
    try {
        const token = (localStorage.getItem('driver_access_token') || localStorage.getItem('access_token'));
        const btn = document.querySelector('#driver-status-badge button');
        btn.innerText = 'Verifying...';
        btn.disabled = true;
        
        await fetch('/api/driver/force_verify', {
            method: 'POST',
            headers: token ? { 'Authorization': Bearer  } : {}
        });
        
        setTimeout(() => {
            btn.innerText = 'Manual Verify';
            btn.disabled = false;
        }, 3000);
    } catch(e) {
        console.error('Failed to force verify', e);
    }
}
