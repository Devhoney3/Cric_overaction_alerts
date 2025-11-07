import os
import time
import requests
from datetime import datetime
from flask import Flask, jsonify
import logging
from threading import Thread

# Configuration
CRICKET_API_KEY = os.getenv('CRICKET_API_KEY', '4c058804-f6ea-414f-b76b-e1e3b9a389ca')
CRICKET_API_BASE = 'https://api.cricketdata.org'  # Free tier available
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7500722570:AAHuAw_osaLg5dKfv9Nq7-fM3ShTBPFSvPc')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7500722570')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
CHECK_INTERVAL = 60  # seconds - fast polling for live betting

# Win probability brackets (from your research)
PROBABILITY_BRACKETS = {
    'unrestricted': (0, 100),
    'very_competitive': (40, 60),  # Best ROI: 20.8%
    'competitive': (35, 65),
    'moderate': (30, 70),
}

# Flask app for health checks and web interface
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "message": "Cricket Betting Alert System"
    })

# Track processed wickets to avoid duplicate alerts
processed_wickets = set()
active_matches = {}

class CricketMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {CRICKET_API_KEY}'
        })
    
    def get_live_matches(self):
        """Fetch all live cricket matches"""
        try:
            response = self.session.get(
                f'{CRICKET_API_BASE}/v1/matches/live',
                timeout=5
            )
            response.raise_for_status()
            return response.json().get('data', [])
        except Exception as e:
            logger.error(f"Error fetching live matches: {e}")
            return []
    
    def get_match_details(self, match_id):
        """Get detailed match information including ball-by-ball"""
        try:
            response = self.session.get(
                f'{CRICKET_API_BASE}/v1/match/{match_id}',
                timeout=5
            )
            response.raise_for_status()
            return response.json().get('data', {})
        except Exception as e:
            logger.error(f"Error fetching match {match_id}: {e}")
            return {}
    
    def calculate_win_probability(self, match_data):
        """
        Calculate win probability based on match situation
        Simplified model - you can enhance with ML model
        """
        try:
            innings = match_data.get('innings', [])
            if not innings:
                return 50.0  # Equal probability if no data
            
            current_innings = innings[-1]
            runs = current_innings.get('runs', 0)
            wickets = current_innings.get('wickets', 0)
            overs = current_innings.get('overs', 0)
            
            # Get target if chasing
            target = match_data.get('target', 0)
            
            if target > 0:  # Chasing scenario
                required_runs = target - runs
                balls_remaining = (50 - overs) * 6  # Assuming ODI
                wickets_remaining = 10 - wickets
                
                # Simple probability model
                if wickets_remaining == 0:
                    return 0.0
                if required_runs <= 0:
                    return 100.0
                
                # Required run rate vs current resources
                required_rr = (required_runs / balls_remaining) * 6 if balls_remaining > 0 else 999
                current_rr = (runs / (overs * 6)) * 6 if overs > 0 else 0
                
                # Probability calculation (simplified DLS-like)
                resources_factor = wickets_remaining / 10
                rr_factor = max(0, 1 - (required_rr / 12))  # 12 is max achievable RR
                momentum_factor = min(current_rr / required_rr, 2) if required_rr > 0 else 1
                
                probability = (resources_factor * 0.4 + rr_factor * 0.4 + 
                             (momentum_factor * 0.2)) * 100
                
                return max(0, min(100, probability))
            
            else:  # First innings
                # Estimate based on runs and wickets
                wickets_factor = (10 - wickets) / 10
                runs_factor = min(runs / 300, 1)  # 300 as par score
                
                probability = (wickets_factor * 0.6 + runs_factor * 0.4) * 100
                return max(0, min(100, probability))
                
        except Exception as e:
            logger.error(f"Error calculating probability: {e}")
            return 50.0
    
    def check_wicket_condition(self, match_data, match_id):
        """Check if wicket just fell and meets strategy criteria"""
        try:
            innings = match_data.get('innings', [])
            if not innings:
                return None
            
            current_innings = innings[-1]
            wickets = current_innings.get('wickets', 0)
            overs = current_innings.get('overs', 0)
            
            # Create unique wicket identifier
            wicket_id = f"{match_id}_inning_{len(innings)}_wicket_{wickets}"
            
            # Check if this is a new wicket
            if wicket_id in processed_wickets:
                return None
            
            # Only process if it's first innings (as per research)
            if len(innings) > 1:
                return None
            
            # Calculate win probability
            win_prob = self.calculate_win_probability(match_data)
            
            # Check if probability falls in target bracket
            for bracket_name, (min_prob, max_prob) in PROBABILITY_BRACKETS.items():
                if min_prob <= win_prob <= max_prob:
                    opportunity = {
                        'match_id': match_id,
                        'wicket_id': wicket_id,
                        'match_name': f"{match_data.get('team1', 'Team1')} vs {match_data.get('team2', 'Team2')}",
                        'wickets': wickets,
                        'overs': overs,
                        'runs': current_innings.get('runs', 0),
                        'win_probability': round(win_prob, 2),
                        'bracket': bracket_name,
                        'timestamp': datetime.now().isoformat(),
                        'innings': 1
                    }
                    
                    # Mark as processed
                    processed_wickets.add(wicket_id)
                    
                    return opportunity
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking wicket condition: {e}")
            return None

class AlertManager:
    @staticmethod
    def send_telegram_alert(opportunity):
        """Send alert via Telegram"""
        try:
            message = f"""
🚨 **BETTING OPPORTUNITY DETECTED** 🚨

📊 Match: {opportunity['match_name']}
🎯 Wicket #{opportunity['wickets']} fell at {opportunity['overs']} overs
💰 Score: {opportunity['runs']}/{opportunity['wickets']}
📈 Win Probability: {opportunity['win_probability']}%
🎲 Strategy Bracket: {opportunity['bracket'].replace('_', ' ').title()}

⚡ EXPECTED RETURN: {20.8 if opportunity['bracket'] == 'very_competitive' else 12.3}%

⏰ Time: {opportunity['timestamp']}

🎯 ACTION: Place lay bet on bowling team NOW!
            """
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'Markdown'
            }
            
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"Telegram alert sent for {opportunity['match_name']}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending Telegram alert: {e}")
            return False
    
    @staticmethod
    def send_discord_alert(opportunity):
        """Send alert via Discord webhook"""
        if not DISCORD_WEBHOOK_URL:
            return False
        
        try:
            embed = {
                "embeds": [{
                    "title": "🚨 BETTING OPPORTUNITY DETECTED",
                    "description": f"Wicket fall in {opportunity['match_name']}",
                    "color": 3447003,  # Blue color
                    "fields": [
                        {"name": "Wicket", "value": f"#{opportunity['wickets']}", "inline": True},
                        {"name": "Overs", "value": str(opportunity['overs']), "inline": True},
                        {"name": "Score", "value": f"{opportunity['runs']}/{opportunity['wickets']}", "inline": True},
                        {"name": "Win Probability", "value": f"{opportunity['win_probability']}%", "inline": True},
                        {"name": "Expected ROI", "value": f"{20.8 if opportunity['bracket'] == 'very_competitive' else 12.3}%", "inline": True},
                        {"name": "Action", "value": "Place lay bet NOW!", "inline": False}
                    ],
                    "timestamp": opportunity['timestamp']
                }]
            }
            
            response = requests.post(DISCORD_WEBHOOK_URL, json=embed, timeout=5)
            response.raise_for_status()
            logger.info(f"Discord alert sent for {opportunity['match_name']}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending Discord alert: {e}")
            return False
    
    @staticmethod
    def send_all_alerts(opportunity):
        """Send alerts via all configured channels"""
        AlertManager.send_telegram_alert(opportunity)
        AlertManager.send_discord_alert(opportunity)

def monitor_loop():
    """Main monitoring loop"""
    monitor = CricketMonitor()
    logger.info("Starting cricket monitoring loop...")
    
    while True:
        try:
            # Get all live matches
            live_matches = monitor.get_live_matches()
            logger.info(f"Monitoring {len(live_matches)} live matches")
            
            for match in live_matches:
                match_id = match.get('id')
                if not match_id:
                    continue
                
                # Get detailed match data
                match_data = monitor.get_match_details(match_id)
                
                # Check for wicket opportunity
                opportunity = monitor.check_wicket_condition(match_data, match_id)
                
                if opportunity:
                    logger.info(f"OPPORTUNITY FOUND: {opportunity}")
                    AlertManager.send_all_alerts(opportunity)
            
            # Wait before next check
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            time.sleep(CHECK_INTERVAL)

# Flask routes
@app.route('/status')
def status():
    """Additional status endpoint with more details"""
    return jsonify({
        'service': 'Cricket Betting Alert System',
        'active_matches': len(active_matches),
        'processed_wickets': len(processed_wickets)
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/stats')
def stats():
    return jsonify({
        'processed_wickets': len(processed_wickets),
        'active_matches': active_matches,
        'uptime': 'running'
    })

if __name__ == '__main__':
    # Start monitoring in background thread
    monitor_thread = Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start Flask app
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
