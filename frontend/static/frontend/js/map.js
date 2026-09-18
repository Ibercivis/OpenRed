// Wait for the DOM to be fully loaded
document.addEventListener('DOMContentLoaded', function () {

    const MAPBOX_ACCESS_TOKEN = window.MAPBOX_ACCESS_TOKEN;  // Replace with your Mapbox token
    const csrfToken = "{{ csrf_token }}";
    let allData = [];
    let slider;  // Declare slider variable outside of fetch
    let sliderIntensity;  // Declare slider variable outside of fetch

    // deck.gl layers kept at module scope so the spectra layer survives the
    // periodic measurement refresh (updateMap rebuilds the scatterplot layer).
    let scatterplotLayer = null;
    let spectraLayer = null;

    // Initialize the Deck.gl map
    const deckgl = new deck.DeckGL({
        container: 'map-container',
        mapboxApiAccessToken: MAPBOX_ACCESS_TOKEN,
        mapStyle: 'mapbox://styles/mapbox/light-v9',
        // mapStyle: 'mapbox://styles/frasanz/cm22xixvn002501o20b0ehcdw',
        initialViewState: {
            longitude: -0.88,
            latitude: 41.64,
            zoom: 12,
            pitch: 45,
            bearing: 0
        },
        controller: true
    });

    // Function to fetch data from API
    function fetchData() {
        return fetch('/api/measurements/', {
            headers: {
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
            .then(response => response.json())
            .then(data => {
                // Convert dateTime to timestamp for easier comparison
                allData = data.map(item => ({
                    ...item,
                    dateTime: new Date(item.dateTime).getTime()
                }));

                if (!slider) {
                    // Initialize slider if it's the first time data is loaded
                    setSliderRange(allData);
                }

                if (!sliderIntensity) {
                    setSliderIntensity(allData);
                }

                // Apply filter based on current slider range
                const [startTimestamp, endTimestamp] = slider.noUiSlider.get().map(v => new Date(v).getTime());
                const filteredData = filterDataByTimestamp(startTimestamp, endTimestamp);
                updateMap(filteredData);
            })
            .catch(error => {
                console.error('Error fetching data:', error);
            });
    }

    // Function to filter data by timestamp
    function filterDataByTimestamp(startTimestamp, endTimestamp) {
        return allData.filter(measurement => {
            return measurement.dateTime >= startTimestamp && measurement.dateTime <= endTimestamp;
        });
    }

    // Function to filter data by radiation value
    function filterDataByRadiation(startRadiation, endRadiation) {
        return allData.filter(measurement => {
            return measurement.values.radiation >= startRadiation && measurement.values.radiation <= endRadiation;
        });
    }

    // Function to update the map with data
    // Function to update the map with data and color gradient based on radiation value
    function updateMap(data) {
        const points = data.map(measurement => {
            // Define the radiation value range
            const minRadiation = 50;
            const maxRadiation = 100;  // Radiation over 80 will be fully red

            // Clamp the radiation value to stay within the range
            const radiation = Math.max(minRadiation, Math.min(maxRadiation, measurement.values.radiation));

            // Calculate the interpolation factor (0 means fully green, 1 means fully red)
            const t = (radiation - minRadiation) / (maxRadiation - minRadiation);

            // Interpolate between green and red
            const color = [
                Math.round((1 - t) * 0 + t * 155 + 100),  // Red channel (100 to 255)
                Math.round((1 - t) * 155 + t * 0 + 100),  // Green channel (255 to 100)
                100  // Blue channel is constant (100)
            ];

            return {
                position: [measurement.longitude, measurement.latitude],
                values: measurement.values,
                dateTime: measurement.dateTime,
                size: 10,
                color: color  // Use the interpolated color
            };
        });

        scatterplotLayer = new deck.ScatterplotLayer({
            id: 'scatterplot-layer',
            data: points,
            getPosition: d => d.position,
            getRadius: d => d.size,
            getFillColor: d => d.color,
            radiusMinPixels: 5,
            radiusMaxPixels: 10,
            pickable: true,
            onHover: ({ object, x, y }) => handleHover(object, x, y)
        });

        renderLayers();
    }

    // Push the current set of layers to deck.gl. Keeping this in one place lets
    // the measurement layer and the spectra layer coexist and update independently.
    function renderLayers() {
        deckgl.setProps({
            layers: [scatterplotLayer, spectraLayer].filter(Boolean)
        });
    }

    // Initialize the datetime range slider
    const startValueEl = document.getElementById('start-value');
    const endValueEl = document.getElementById('end-value');

    function setSliderRange(data) {
        // Get the minimum and maximum timestamp from the data
        const minTimestamp = Math.min(...data.map(item => item.dateTime));
        // For maxTimestamp, we want 6 hours from the current time
        const maxTimestamp = new Date().getTime() + 6 * 60 * 60 * 1000;
        slider = document.getElementById('slider');  // Initialize slider

        noUiSlider.create(slider, {
            start: [minTimestamp, maxTimestamp],
            connect: true,
            range: {
                min: minTimestamp,
                max: maxTimestamp
            },
            tooltips: false,
            format: {
                to: value => new Date(value), // Display as readable datetime
                from: value => value
            }
        });

        // Update slider labels and filter data
        slider.noUiSlider.on('update', function (values, handle) {
            const startTimestamp = new Date(values[0]).getTime();
            const endTimestamp = new Date(values[1]).getTime();

            // Update labels
            startValueEl.innerHTML = new Date(startTimestamp).toLocaleString();
            endValueEl.innerHTML = new Date(endTimestamp).toLocaleString();

            // Filter data and update the map
            const filteredData = filterDataByTimestamp(startTimestamp, endTimestamp);
            updateMap(filteredData);
        });
    }

    // Initialize the intensity range slider
    const intensityStartValueEl = document.getElementById('intensity-start-value');
    const intensityEndValueEl = document.getElementById('intensity-end-value');

    function setSliderIntensity(data) {
        // Get the minimum and maximum timestamp from the data
        const minRadiation = 0;
        const maxRadiation = 1000;
        sliderIntensity = document.getElementById('slider-intensity');  // Initialize slider
        console.log(minRadiation, maxRadiation);
        console.log("sliderIntensity", sliderIntensity);
        noUiSlider.create(sliderIntensity, {
            start: [minRadiation, maxRadiation],
            connect: true,
            range: {
                min: minRadiation,
                max: maxRadiation
            },
            tooltips: false,
            format: {
                to: value => value, // Display as readable datetime
                from: value => value
            }
        });

        // Update slider labels and filter data
        sliderIntensity.noUiSlider.on('update', function (values, handle) {
            const startRadiation = values[0];
            const endRadiation = values[1];

            // Update labels
            intensityStartValueEl.innerHTML = startRadiation;
            intensityEndValueEl.innerHTML = endRadiation;

            // Filter data and update the map
            const filteredData = filterDataByRadiation(startRadiation, endRadiation);
            updateMap(filteredData);
        });
    }

    // Function to handle hover and display popup
    function handleHover(object, x, y) {
        const popup = document.getElementById('popup');
        const popupContent = document.getElementById('popup-content');

        if (object) {
            // check if the object is valid
            if (!object.values) {
                object.values = {};
            }
            popupContent.innerHTML = `
            <strong>Radiation Level:</strong> ${object.values.radiation}<br/>
            <strong>Location:</strong> (${object.position[0]}, ${object.position[1]})<br/>
            <strong>Date:</strong> ${new Date(object.dateTime).toLocaleString()}
        `;

            // Position the popup near the mouse
            popup.style.left = `${x}px`;
            popup.style.top = `${y}px`;
            popup.style.display = 'block';  // Show the popup
        } else {
            console.log('No object');
            popup.style.display = 'none';  // Hide the popup when not hovering
        }
    }

    // ===================== Spectrum visualizer =====================
    // Replicates the app's spectrum_chart.dart rendering with ECharts.
    const MAX_ENERGY_KEV = 1800;
    const ISOTOPE_LINES = [
        { e: 238.6,  label: 'Pb-212' },
        { e: 351.9,  label: 'Pb-214' },
        { e: 511,    label: 'β+' },
        { e: 609.3,  label: 'Bi-214' },
        { e: 661.7,  label: 'Cs-137' },
        { e: 911.2,  label: 'Ac-228' },
        { e: 1173,   label: 'Co-60' },
        { e: 1332.5, label: 'Co-60' },
        { e: 1460.8, label: 'K-40' },
        { e: 1764.5, label: 'Bi-214' },
        { e: 2614.5, label: 'Tl-208' },
    ];
    const CHART_BG = '#ffffff';
    const PRIMARY = '#0d6efd';                       // curve color
    const PRIMARY_FILL = 'rgba(13,110,253,0.12)';    // 12% fill under curve

    let spectrumChart = null;
    let currentSpectrum = null;
    let chartLogScale = true;                         // default: log scale

    // Counts label formatter (k / M), matching the app's fmtCount.
    function fmtCount(v) {
        if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M';
        if (v >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + 'k';
        return String(Math.round(v));
    }

    // Load all geolocated spectra as a clickable deck.gl layer.
    function loadSpectraMarkers() {
        fetch('/api/radiation-spectra/map/', { headers: { 'Accept': 'application/json' } })
            .then(r => r.json())
            .then(list => {
                const pts = (list || []).map(s => ({ position: [s.longitude, s.latitude], id: s.id }));
                spectraLayer = new deck.ScatterplotLayer({
                    id: 'spectra-layer',
                    data: pts,
                    getPosition: d => d.position,
                    getRadius: 7,
                    radiusMinPixels: 6,
                    radiusMaxPixels: 14,
                    getFillColor: [150, 45, 220, 220],   // distinct purple for spectra
                    stroked: true,
                    getLineColor: [255, 255, 255],
                    lineWidthMinPixels: 1.5,
                    pickable: true,
                    onClick: ({ object }) => { if (object) openSpectrum(object.id); }
                });
                renderLayers();
            })
            .catch(err => console.error('Error loading spectra markers:', err));
    }

    // Fetch a single spectrum in full detail and open the modal.
    function openSpectrum(id) {
        fetch(`/api/radiation-spectra/${id}/`, { headers: { 'Accept': 'application/json' } })
            .then(r => r.json())
            .then(spec => {
                currentSpectrum = spec;
                document.getElementById('spectrum-title').textContent = spec.name || `Espectro #${spec.id}`;
                const meta = [];
                if (spec.startedAt) meta.push(new Date(spec.startedAt).toLocaleString());
                if (spec.durationSec != null) meta.push(`${spec.durationSec}s`);
                if (spec.channelCount != null) meta.push(`${spec.channelCount} canales`);
                if (spec.startLat != null && spec.startLon != null) {
                    meta.push(`(${spec.startLat.toFixed(5)}, ${spec.startLon.toFixed(5)})`);
                }
                document.getElementById('spectrum-meta').textContent = meta.join(' · ');
                bootstrap.Modal.getOrCreateInstance(document.getElementById('spectrum-modal')).show();
            })
            .catch(err => console.error('Error fetching spectrum detail:', err));
    }

    // Render the current spectrum into ECharts following spectrum_chart.dart.
    function renderSpectrum() {
        if (!currentSpectrum) return;
        const el = document.getElementById('spectrum-chart');
        if (!spectrumChart) spectrumChart = echarts.init(el);

        const counts = currentSpectrum.counts || [];
        const n = currentSpectrum.channelCount || counts.length;
        const a0 = currentSpectrum.a0 || 0, a1 = currentSpectrum.a1 || 0, a2 = currentSpectrum.a2 || 0;

        // X axis: energy (keV) via calibration, or channel index if calibration looks invalid.
        const E = ch => a0 + a1 * ch + a2 * ch * ch;
        const hasCalib = Math.abs(E(n - 1) - E(0)) > 1.0;
        const xOf = ch => hasCalib ? E(ch) : ch;
        const minX = xOf(0);
        const maxX = xOf(n - 1);
        const visibleMaxX = (hasCalib && maxX > MAX_ENERGY_KEV) ? MAX_ENERGY_KEV : maxX;
        const xInterval = Math.max(1, (visibleMaxX - minX) / 4);
        const needsScroll = maxX > visibleMaxX + 1e-9;

        // Y axis: log10(count + 1) by default, or raw counts.
        const yOf = chartLogScale ? (c => Math.log10(c + 1)) : (c => c);
        const data = counts.map((c, ch) => [xOf(ch), yOf(c)]);
        let maxY = 0;
        for (const c of counts) { const y = yOf(c); if (y > maxY) maxY = y; }
        const hiY = maxY <= 0 ? 1 : maxY * 1.08;                 // 8% headroom
        const yInterval = chartLogScale ? 1 : Math.max(1, hiY / 4);

        const isotopeLines = hasCalib
            ? ISOTOPE_LINES
                .filter(iso => iso.e >= minX && iso.e <= maxX)
                .map(iso => ({
                    xAxis: iso.e,
                    label: {
                        formatter: iso.label,
                        rotate: 90,
                        position: 'end',
                        color: '#555',
                        fontSize: 10,
                        backgroundColor: CHART_BG,
                        padding: [2, 2]
                    },
                    lineStyle: { type: [4, 3], width: 1, color: 'rgba(128,128,128,0.4)' }
                }))
            : [];

        const option = {
            backgroundColor: CHART_BG,
            animation: false,
            grid: { left: 56, right: 16, top: 16, bottom: needsScroll ? 64 : 40 },
            xAxis: {
                type: 'value',
                name: hasCalib ? 'Energía (keV)' : 'Canal',
                nameLocation: 'middle',
                nameGap: 28,
                min: minX,
                max: maxX,
                interval: xInterval,
                axisLabel: { formatter: v => Math.round(v) }
            },
            yAxis: {
                type: 'value',
                min: 0,
                max: hiY,
                interval: yInterval,
                axisLabel: { formatter: v => fmtCount(chartLogScale ? Math.pow(10, v) : v) }
            },
            series: [{
                type: 'line',
                data: data,
                smooth: false,
                showSymbol: false,
                lineStyle: { width: 1, color: PRIMARY },
                areaStyle: { color: PRIMARY_FILL },
                markLine: { silent: true, symbol: 'none', data: isotopeLines }
            }]
        };

        // Cap the viewport at 1800 keV; the rest is reachable by scroll/pan.
        if (needsScroll) {
            option.dataZoom = [
                { type: 'inside', filterMode: 'none', startValue: minX, endValue: visibleMaxX },
                { type: 'slider', filterMode: 'none', startValue: minX, endValue: visibleMaxX, height: 16, bottom: 36 }
            ];
        }

        spectrumChart.setOption(option, true);   // notMerge: fully refresh on toggle
        spectrumChart.resize();
    }

    // Wire modal lifecycle + log/linear toggle once.
    const spectrumModalEl = document.getElementById('spectrum-modal');
    if (spectrumModalEl) {
        spectrumModalEl.addEventListener('shown.bs.modal', renderSpectrum);
    }
    const btnLog = document.getElementById('spectrum-scale-log');
    const btnLin = document.getElementById('spectrum-scale-lin');
    if (btnLog && btnLin) {
        btnLog.addEventListener('click', () => {
            chartLogScale = true;
            btnLog.classList.add('active');
            btnLin.classList.remove('active');
            renderSpectrum();
        });
        btnLin.addEventListener('click', () => {
            chartLogScale = false;
            btnLin.classList.add('active');
            btnLog.classList.remove('active');
            renderSpectrum();
        });
    }
    window.addEventListener('resize', () => { if (spectrumChart) spectrumChart.resize(); });

    // Fetch data every 10 seconds
    setInterval(fetchData, 10000);

    // Fetch data initially when the page loads
    fetchData();

    // Load clickable spectra markers
    loadSpectraMarkers();
}); // End of DOMContentLoaded