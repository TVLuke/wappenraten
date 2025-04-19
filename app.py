from flask import Flask, jsonify, session, request, send_file, send_from_directory
from SPARQLWrapper import SPARQLWrapper, JSON
from datetime import timedelta, datetime
import json
import os
import random
import uuid
from pathlib import Path
import requests
import uuid
from io import BytesIO

# Store municipalities data globally
municipalities = []

# Store image mappings
image_mappings = {}

USER_DATA_DIR = 'data/users'

class UserGameData:
    def __init__(self):
        self.data_dir = Path(USER_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def get_user_file(self, user_id):
        return self.data_dir / f"{user_id}.json"
    
    def get_user_data(self, user_id):
        try:
            if os.path.getsize(self.get_user_file(user_id)) > 0:
                with open(self.get_user_file(user_id)) as f:
                    return json.load(f)
            return {'correct': 0, 'wrong': 0, 'history': [], 'used_municipalities': []}
        except (FileNotFoundError, OSError):
            return {'correct': 0, 'wrong': 0, 'history': [], 'used_municipalities': []}
    
    def save_user_data(self, user_id, data):
        with open(self.get_user_file(user_id), 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def create_app():
    app = Flask(__name__)
    app.secret_key = 'wappen_ratespiel_secret'  # Change this in production
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
    return app

app = create_app()

def generate_image_id():
    return str(uuid.uuid4())

CACHE_FILE = 'data/municipalities_cache.json'

def is_cache_valid():
    """Check if the cache file exists and is less than 24 hours old."""
    if not os.path.exists(CACHE_FILE):
        return False
    
    file_time = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
    age = datetime.now() - file_time
    return age.total_seconds() < 24 * 3600

def load_cache():
    """Load municipalities data from cache file."""
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
            global image_mappings
            image_mappings.clear()
            image_mappings.update(cache_data['image_mappings'])
            return cache_data['municipalities']
    except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        print(f'Error loading cache: {str(e)}')
        return None

def save_cache(municipalities, mappings):
    """Save municipalities data and image mappings to cache file."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        cache_data = {
            'municipalities': municipalities,
            'image_mappings': mappings,
            'timestamp': datetime.now().isoformat()
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'Error saving cache: {str(e)}')

def query_wikidata():
    """Fetch municipalities data from Wikidata via SPARQL."""
    sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
    # Add User-Agent header to avoid 403 errors
    sparql.addCustomHttpHeader('User-Agent', 'WappenRatespiel/1.0 (https://github.com/TVLuke/wappenraten)')
    sparql.setQuery("""
SELECT DISTINCT ?municipality ?municipalityLabel ?coatOfArms ?coatOfArmsText ?article
WHERE {
    # Zuerst Filter für Deutschland anwenden
    ?municipality wdt:P17 wd:Q183;  # Muss in Deutschland sein

    # Dann den Typ der Gebietskörperschaft festlegen
    VALUES ?type { 
        wd:Q56061      # Gemeinde
        wd:Q262166     # Stadt
        wd:Q200250     # Landkreis
        wd:Q17715806   # Kreis
        wd:Q160091     # Regierungsbezirk
        wd:Q2054788    # Verbandsgemeinde
        wd:Q484170     # Amt
        wd:Q1799794    # Samtgemeinde
        wd:Q15893266   # Verband
        wd:Q1895745    # Verwaltungsgemeinschaft
        wd:Q6501447    # Stadtbezirk
        wd:Q82794      # Ortsteil
        wd:Q14952619   # Land
        wd:Q1802976    # Marktgemeinde
        wd:Q2792234    # Kreisfreie Stadt
        wd:Q15903294   # Verbandsgemeinde
        wd:Q2381679    # Regionale Verwaltungseinheit
        wd:Q2067852    # Stadtteil
        wd:Q671
        wd:Q224936     # Landkreis (Untertyp)
        wd:Q1663803    # Gemeindeverwaltung
        wd:Q42744322   # Städtische Gemeinde in Deutschland
        wd:Q116457956  # non-urban municipality in Germany
    }
    
    ?municipality wdt:P31 ?type ;       
                  wdt:P94 ?coatOfArms ;  
                  wdt:P17 wd:Q183;  # Muss in Deutschland sein
                  rdfs:label ?municipalityLabel .
    
    # Optional das Wappenbild beschreiben, falls vorhanden
    OPTIONAL { ?municipality wdt:P237 ?coatOfArmsText. }
    
    # Deutsche Wikipedia-Artikel URL holen
    OPTIONAL {
        ?article schema:about ?municipality ;
                schema:isPartOf <https://de.wikipedia.org/> ;
                schema:name ?articleName .
    }
    
    FILTER(LANG(?municipalityLabel) = "de")
}
ORDER BY ?municipalityLabel
    """)
    sparql.setReturnFormat(JSON)
    
    try:
        print('Executing SPARQL query...')
        results = sparql.query().convert()
        print('Query executed successfully')
    except Exception as e:
        print(f'Error executing SPARQL query: {str(e)}')
        print(f'Error type: {type(e).__name__}')
        raise
    
    municipalities = []
    global image_mappings
    image_mappings.clear()
    
    print('=== FETCHING MUNICIPALITIES ===')
    for result in results['results']['bindings']:
        # Skip unwanted entries
        name = result['municipalityLabel']['value']
        if "Panzerartilleriebataillon" in name:
            continue
        image_url = result['coatOfArms']['value']
        image_id = generate_image_id()
        image_mappings[image_id] = image_url
        proxy_url = f'/image/{image_id}'
        
        print(f'Municipality: {result["municipalityLabel"]["value"]}\n'
              f'Original URL: {image_url}\n'
              f'Proxy URL: {proxy_url}\n')
        
        # Get coat of arms description if available
        coat_of_arms_desc = result.get('coatOfArmsText', {}).get('value', '') if 'coatOfArmsText' in result else ''
        
        # Get Wikipedia article URL if available
        wiki_url = result.get('article', {}).get('value', '')
        
        municipalities.append({
            'name': result['municipalityLabel']['value'],
            'coat_of_arms': proxy_url,
            'coat_of_arms_desc': coat_of_arms_desc,
            'wiki_url': wiki_url
        })
    
    print(f'Total municipalities: {len(municipalities)}\n'
          f'Total image mappings: {len(image_mappings)}\n')
    return municipalities

def fetch_municipalities():
    """Get municipalities data, either from cache or from Wikidata."""
    if is_cache_valid():
        print('Using cached municipalities data')
        cached_data = load_cache()
        if cached_data is not None:
            return cached_data
        print('Cache invalid or corrupted, fetching from Wikidata')
    
    print('Fetching municipalities from Wikidata')
    municipalities = query_wikidata()
    save_cache(municipalities, image_mappings)
    return municipalities

@app.route('/image/<image_id>')
def get_image(image_id):
    print(f'=== GET IMAGE ===\nRequested ID: {image_id}')
    
    if image_id not in image_mappings:
        print(f'Image ID {image_id} not found in mappings')
        return 'Image not found', 404
    
    original_url = image_mappings[image_id]
    print(f'Fetching image from: {original_url}')
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    try:
        # Get the original image from Wikipedia
        response = requests.get(original_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        print(f'Successfully fetched image:')
        print(f'Status Code: {response.status_code}')
        print(f'Content-Type: {response.headers.get("Content-Type")}')
        print(f'Content Length: {len(response.content)} bytes')
        
        # Send the image data
        return send_file(
            BytesIO(response.content),
            mimetype=response.headers.get('Content-Type', 'image/svg+xml')
        )
        
    except requests.RequestException as e:
        print(f'Failed to fetch image: {str(e)}')
        print(f'Error type: {type(e).__name__}')
        return f'Failed to load image: {str(e)}', 500

def initialize():
    global municipalities
    municipalities = fetch_municipalities()

# Initialize municipalities on startup
with app.app_context():
    municipalities = fetch_municipalities()

game_data = UserGameData()

@app.before_request
def ensure_user_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())


@app.route('/api/puzzle')
def get_puzzle():
    print('=== GET PUZZLE ===')
    user_id = session['user_id']
    user_data = game_data.get_user_data(user_id)
    
    # Check if there's a current puzzle that hasn't been answered yet
    if 'current_puzzle' in user_data:
        print('Re-serving existing puzzle')
        current_puzzle = user_data['current_puzzle']
        return jsonify({
            'image_url': current_puzzle['image_url'],
            'image_desc': current_puzzle['image_desc'],
            'options': current_puzzle['options'],
            'stats': {
                'correct': user_data.get('correct', 0),
                'wrong': user_data.get('wrong', 0)
            },
            'history': user_data.get('history', [])
        })
    
    # Get list of unused municipalities
    used = set(user_data.get('used_municipalities', []))
    available = [m for m in municipalities if m['name'] not in used]
    
    # If all municipalities have been used, reset the list
    if not available:
        available = municipalities
        user_data['used_municipalities'] = []
    
    # Select correct answer and generate options
    correct_municipality = random.choice(available)
    correct_answer = correct_municipality['name']
    user_data['used_municipalities'].append(correct_answer)
    
    # Generate wrong options
    other_names = [m['name'] for m in municipalities if m['name'] != correct_answer]
    wrong_options = random.sample(other_names, min(9, len(other_names)))
    
    # Combine and shuffle options
    options = [correct_answer] + wrong_options
    random.shuffle(options)
    
    # Store the current puzzle in user data
    user_data['current_puzzle'] = {
        'image_url': correct_municipality['coat_of_arms'],
        'image_desc': correct_municipality['coat_of_arms_desc'],
        'options': options,
        'correct_answer': correct_answer
    }
    
    game_data.save_user_data(user_id, user_data)
    
    return jsonify({
        'image_url': correct_municipality['coat_of_arms'],
        'image_desc': correct_municipality['coat_of_arms_desc'],
        'options': options,
        'stats': {
            'correct': user_data.get('correct', 0),
            'wrong': user_data.get('wrong', 0)
        },
        'history': user_data.get('history', [])
    })

@app.route('/api/submit', methods=['POST'])
def submit_answer():
    print('=== SUBMIT ANSWER ===')
    user_id = session['user_id']
    user_data = game_data.get_user_data(user_id)
    
    # Get the user's answer from the request
    data = request.get_json()
    user_answer = data.get('answer')
    
    # Check if there's a current puzzle
    if 'current_puzzle' not in user_data:
        return jsonify({'error': 'No puzzle in progress'}), 400
    
    # Get the correct answer from the current puzzle
    correct_answer = user_data['current_puzzle']['correct_answer']
    
    is_correct = user_answer == correct_answer
    
    # Update stats
    if is_correct:
        user_data['correct'] = user_data.get('correct', 0) + 1
    else:
        user_data['wrong'] = user_data.get('wrong', 0) + 1
    
    # Find the correct municipality to get its wiki_url
    correct_municipality = next(
        m for m in municipalities 
        if m['name'] == correct_answer
    )
    
    # Check if municipality is already in history
    history = user_data.get('history', [])
    if not any(entry['correct_answer'] == correct_answer for entry in history):
        # Only add to history if not already present
        history_entry = {
            'image_url': user_data['current_puzzle']['image_url'],
            'wiki_url': correct_municipality['wiki_url'],
            'correct_answer': correct_answer,
            'user_answer': user_answer,
            'is_correct': is_correct
        }
        user_data.setdefault('history', []).append(history_entry)
    
    # Clear the current puzzle only if the answer was correct
    # This ensures players can't just refresh to get a new puzzle if they don't know the answer
    if is_correct:
        user_data.pop('current_puzzle', None)
    
    game_data.save_user_data(user_id, user_data)
    
    return jsonify({
        'is_correct': is_correct,
        'correct_answer': correct_answer,
        'stats': {
            'correct': user_data.get('correct', 0),
            'wrong': user_data.get('wrong', 0)
        },
        'history': user_data.get('history', [])
    })

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/list')
def list_view():
    return app.send_static_file('list.html')

@app.route('/api/municipalities')
def get_municipalities():
    return jsonify(fetch_municipalities())

@app.route('/favicon_io/<path:filename>')
def favicon(filename):
    return send_from_directory('favicon_io', filename)

@app.route('/api/reset', methods=['POST'])
def reset_session():
    # Get the user ID before clearing the session
    if 'user_id' in session:
        user_id = session['user_id']
        user_data = game_data.get_user_data(user_id)
        # Clear the current puzzle to force a new one after reset
        if 'current_puzzle' in user_data:
            user_data.pop('current_puzzle', None)
            game_data.save_user_data(user_id, user_data)
    
    session.clear()
    return jsonify({'message': 'Session reset successfully'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
