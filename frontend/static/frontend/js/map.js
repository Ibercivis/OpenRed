// Wait for the DOM to be fully loaded
document.addEventListener('DOMContentLoaded', function () {

    const MAPBOX_ACCESS_TOKEN = window.MAPBOX_ACCESS_TOKEN;  // Replace with your Mapbox token
    const csrfToken = "{{ csrf_token }}";
    let allData = [];
    let slider;  // Declare slider variable outside of fetch
    let sliderIntensity;  // Declare slider variable outside of fetch
    let isHeatmapVisible = false; // Track heatmap visibility

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

    document.getElementById('toggle-heatmap').addEventListener('click', () => {
        isHeatmapVisible = !isHeatmapVisible;
        // Cambiar el texto del botón
        document.getElementById('toggle-heatmap').innerText = isHeatmapVisible ? 'Mostrar puntos' : 'Mostrar mapa de calor';
        // Actualiza la capa
        updateMap(allData);  // currentFilteredData será la última data filtrada
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
                let filteredData = filterDataByTimestamp(startTimestamp, endTimestamp);
                // Apply filter based on current intensity slider range
                const [startRadiation, endRadiation] = sliderIntensity.noUiSlider.get().map(v => parseFloat(v));
                const radiationFilteredData = filterDataByRadiation(startRadiation, endRadiation);
                // Combine both filters
                filteredData = filteredData.filter(measurement => {
                    return radiationFilteredData.some(radiationMeasurement => {
                        return measurement.dateTime === radiationMeasurement.dateTime &&
                            measurement.latitude === radiationMeasurement.latitude &&
                            measurement.longitude === radiationMeasurement.longitude;
                    });
                });
                // Update the map with the filtered data        
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
        console.log("Updating map with data:", data);
        const minRadiation = 50;
        const maxRadiation = 200; 
        if (isHeatmapVisible) {
            // Capa de calor
            const heatmapLayer = new deck.HeatmapLayer({
                id: 'heatmap-layer',
                data: data,
                getPosition: d => [d.longitude, d.latitude],
                getWeight: d => {
                    const r = d.values.radiation;
                    return Math.max(minRadiation, Math.min(maxRadiation, r));
                },
                colorDomain: [50, 200], 
                radiusPixels: 60,
                aggregation: 'MEAN',
                
                colorRange: [
                    [100, 255, 100],  // Verde (50)
                    [162, 215, 100],  // Amarillo
                    [224, 175, 100],  // Naranja
                    [255, 155, 100]   // Rojo (200)
                ]
            });
    
            deckgl.setProps({ layers: [heatmapLayer] });
    
        } else {
            const points = data.map(measurement => {
                // Define the radiation value range
                 // Radiation over 80 will be fully red

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

            const scatterplotLayer = new deck.ScatterplotLayer({
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

            deckgl.setProps({
                layers: [scatterplotLayer]
            });
        }    
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

    // Fetch data every 10 seconds
    setInterval(fetchData, 10000);

    // Fetch data initially when the page loads
    fetchData();
}); // End of DOMContentLoaded