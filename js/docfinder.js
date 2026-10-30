let doctorsData;
let map;
let markers = [];

async function fetchDoctorsData() {
    const response = await fetch('data/docfinder.json');
    doctorsData = await response.json();
}

function getLocation() {
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(showPosition);
    } else {
        alert("Geolocation is not supported by this browser.");
    }
}

function showPosition(position) {
    const lat = position.coords.latitude;
    const lon = position.coords.longitude;
    findNearestDoctors(lat, lon);
}

function manualSearch() {
    const address = document.getElementById("address").value.toLowerCase();
    if (address) {
        const matchedDoctors = doctorsData.filter(doctor => doctor.Address.toLowerCase().includes(address));
        displayDoctors(matchedDoctors);
        if (matchedDoctors.length > 0) {
            showMap(matchedDoctors[0].Latitude, matchedDoctors[0].Longitude, matchedDoctors);
        } else {
            document.getElementById('map').innerHTML = 'No doctor found';
        }
    }
}

function findNearestDoctors(lat, lon) {
    const distances = doctorsData.map(doctor => {
        const distance = getDistanceFromLatLonInKm(lat, lon, doctor.Latitude, doctor.Longitude);
        return { ...doctor, distance };
    });

    distances.sort((a, b) => a.distance - b.distance);
    const nearestDoctors = distances.slice(0, 10);
    displayDoctors(nearestDoctors);
    showMap(lat, lon, nearestDoctors);
}

function getDistanceFromLatLonInKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = deg2rad(lat2 - lat1);
    const dLon = deg2rad(lon2 - lon1);
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(deg2rad(lat1)) * Math.cos(deg2rad(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

function deg2rad(deg) {
    return deg * (Math.PI / 180);
}

function displayDoctors(doctors) {
    const resultsDiv = document.getElementById('results');
    resultsDiv.innerHTML = '';
    if (doctors.length === 0) {
        resultsDiv.innerHTML = '<p>No doctor found</p>';
        return;
    }
    doctors.forEach(doctor => {
        const div = document.createElement('div');
        div.className = 'result-box';
        div.innerHTML = `
            <h3>${doctor.Name}</h3>
            <p>Contact: ${doctor["Contact Number"]}</p>
            <p>Address: ${doctor.Address}</p>
        `;
        resultsDiv.appendChild(div);
    });
}

function showMap(lat, lon, doctors) {
    if (map) {
        map.remove();
    }
    map = L.map('map').setView([lat, lon], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    markers.forEach(marker => map.removeLayer(marker));
    markers = [];

    doctors.forEach(doctor => {
        const marker = L.marker([doctor.Latitude, doctor.Longitude]).addTo(map)
            .bindPopup(`<b>${doctor.Name}</b><br>${doctor.Address}`).openPopup();
        markers.push(marker);
    });

    if (doctors.length > 0) {
        const directions = L.Routing.control({
            waypoints: [
                L.latLng(lat, lon),
                L.latLng(doctors[0].Latitude, doctors[0].Longitude)
            ]
        }).addTo(map);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    await fetchDoctorsData();
});