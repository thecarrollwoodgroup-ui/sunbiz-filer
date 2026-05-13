import os
from flask import Flask, render_template, jsonify
from datetime import datetime
import pytz
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')

# Define time zones
TIMEZONES = {
    'UTC': 'UTC',
    'EST': 'US/Eastern',
    'CST': 'US/Central',
    'MST': 'US/Mountain',
    'PST': 'US/Pacific',
    'GMT': 'Europe/London',
    'CET': 'Europe/Paris',
    'IST': 'Asia/Kolkata',
    'JST': 'Asia/Tokyo',
    'AEST': 'Australia/Sydney',
    'NZST': 'Pacific/Auckland',
    'SGT': 'Asia/Singapore',
}

@app.route('/')
def index():
    """Render the digital clock page."""
    return render_template('clock.html', timezones=TIMEZONES)

@app.route('/api/time')
def get_time():
    """Get current time in all configured time zones."""
    time_data = {}
    
    for abbreviation, timezone in TIMEZONES.items():
        tz = pytz.timezone(timezone)
        current_time = datetime.now(tz)
        
        time_data[abbreviation] = {
            'timezone': timezone,
            'time': current_time.strftime('%H:%M:%S'),
            'time_12h': current_time.strftime('%I:%M:%S %p'),
            'date': current_time.strftime('%A, %B %d, %Y'),
            'offset': current_time.strftime('%z'),
        }
    
    return jsonify(time_data)

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
