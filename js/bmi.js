document.addEventListener("DOMContentLoaded", function() {
    const form = document.getElementById('bmi-form');
    const resultDiv = document.getElementById('bmi-result');
    const chartCanvas = document.getElementById('bmi-chart');
    let bmiChart;

    form.addEventListener('submit', function(event) {
        event.preventDefault();
        const height = parseFloat(document.getElementById('height').value);
        const weight = parseFloat(document.getElementById('weight').value);

        if (isNaN(height) || isNaN(weight) || height <= 0 || weight <= 0) {
            resultDiv.innerHTML = '<p>Please enter valid height and weight values.</p>';
            chartCanvas.style.display = 'none';
            return;
        }

        const bmi = calculateBMI(height, weight);
        displayBMIResult(bmi);
        generateBMIChart(height, weight, bmi);
    });

    function calculateBMI(height, weight) {
        return (weight / Math.pow(height / 100, 2)).toFixed(1);
    }

    function displayBMIResult(bmi) {
        let result = '';
        let status = '';

        if (bmi < 18.5) {
            status = 'Underweight';
        } else if (bmi < 25) {
            status = 'Normal';
        } else if (bmi < 30) {
            status = 'Overweight';
        } else {
            status = 'Obesity';
        }

        result += `<p>Your BMI: ${bmi}</p>`;
        result += `<p>Status: ${status}</p>`;
        
        resultDiv.innerHTML = result;
    }

    function generateBMIChart(height, weight, bmi) {
        const ctx = chartCanvas.getContext('2d');
        chartCanvas.style.display = 'block';

        if (bmi < 10 || bmi > 60) {
            resultDiv.innerHTML = '<p>Please enter realistic height and weight values.</p>';
            chartCanvas.style.display = 'none';
            return;
        }

        if (bmiChart) {
            bmiChart.destroy();
        }

        const userHeight = height;
        const userWeight = weight;

        bmiChart = new Chart(ctx, {
            type: 'scatter',
            data: {
                datasets: [
                    {
                        label: 'Your BMI',
                        data: [{ x: userHeight, y: userWeight }],
                        backgroundColor: 'red',
                        pointRadius: 8,
                        pointHoverRadius: 10,
                        pointStyle: 'circle'
                    }
                ]
            },
            options: {
                scales: {
                    x: {
                        type: 'linear',
                        position: 'bottom',
                        title: {
                            display: true,
                            text: 'Height (cm)'
                        }
                    },
                    y: {
                        title: {
                            display: true,
                            text: 'Weight (kg)'
                        }
                    }
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `Your BMI: ${bmi}`;
                            }
                        }
                    }
                }
            }
        });

        const x = ctx.canvas.width * (userHeight - 150) / (200 - 150);
        const y = ctx.canvas.height * (24.9 - bmi) / (24.9 - 18.5);
        ctx.beginPath();
        ctx.moveTo(x, ctx.canvas.height);
        ctx.lineTo(x, y);
        ctx.lineTo(0, y);
        ctx.strokeStyle = 'black';
        ctx.lineWidth = 1;
        ctx.stroke();
    }
});
