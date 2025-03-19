from flask import Flask, render_template, request, jsonify
from models import db, Show, init_db
from tasks import send_telegram_message
import datetime  # Import the datetime module
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Load configuration from .env file (as before)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tv_shows.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Suppress a warning
app.config['SECRET_KEY'] = 'your_secret_key'  # Use a strong secret key in production
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0' #Or your provider
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0' #Or your provider

# Initialize database (with check for existing tables)
with app.app_context():
    db.init_app(app)
    if not db.engine.dialect.has_table(db.engine, 'show'):
        init_db(app)  # Initialize only if tables don't exist
        logging.info("Database tables created.")
    else:
         logging.info("Database tables already exist.")

# --- Routes ---

@app.route('/')
def index():
    """Displays the homepage with newly added shows and most watched today."""
    try:
        newly_added_shows = Show.query.order_by(Show.added_on.desc()).limit(8).all()
        #For simplicity, get the first 4 for the slideshow. In a real app, use a proper ranking.
        most_watched_today = Show.query.order_by(Show.added_on.desc()).limit(4).all()

        #Separate desktop and mobile slides for responsive design
        desktop_slides = most_watched_today
        mobile_slides = most_watched_today

        return render_template('index.html', newly_added_shows=newly_added_shows, desktop_slides = desktop_slides, mobile_slides=mobile_slides)
    except Exception as e:
        logging.exception("Exception on /")
        return "An error occurred: " + str(e), 500

@app.route('/shows')
def list_shows():
    """Lists available shows with filtering and sorting."""
    try:
        # Get parameters from the request
        sort_by = request.args.get('sort', 'name')  # Default sort by name
        genre = request.args.get('genre', 'all')
        year = request.args.get('year', 'all')
        min_rating = request.args.get('rating', 'all')  # Get rating filter
        page = request.args.get('page', 1, type=int) # Get the page number
        per_page = 12 # Number of the shows per page

        # Build the query
        query = Show.query

        if genre != 'all':
            query = query.filter(Show.genres.contains(genre))  # Use contains for genre list
        if year != 'all':
            query = query.filter(Show.year == int(year))
         #Filter by rating
        if min_rating != 'all':
            try:
                min_rating_float = float(min_rating)
                query = query.filter(Show.rating >= min_rating_float)
            except ValueError:
                pass  # Ignore invalid rating values
        # Apply sorting
        if sort_by == 'name':
            query = query.order_by(Show.name)
        elif sort_by == 'year':
            query = query.order_by(Show.year.desc())
        elif sort_by == 'rating':
            query = query.order_by(Show.rating.desc())

        #Paginate the result
        shows = query.paginate(page=page, per_page=per_page, error_out=False)
        # Get all available genres for the filter (from existing shows)
        all_genres = set()
        for show in Show.query.all():
            all_genres.update(show.genres)  # Use update to add all elements from the list
        all_genres = sorted(list(all_genres))  # Convert back to a sorted list

        now = datetime.datetime.now() # Get the current date and time
        return render_template(
            'shows.html',
            shows=shows,
            all_genres=all_genres,
            sort_by=sort_by,
            current_genre=genre,  # Pass selected genre for highlighting
            current_year=year,    # Pass selected year for highlighting
            current_rating=min_rating,   #Pass selected rating
            now=now  # Pass 'now' to the template
        )
    except Exception as e:
        logging.exception("Exception on /shows")
        return "An error occurred: " + str(e), 500

@app.route('/show/<int:show_id>')
def show_details(show_id):
    """Displays details for a specific show."""
    try:
        show = Show.query.get_or_404(show_id)  # Get show or return 404 if not found
        return render_template('show_details.html', show=show)
    except Exception as e:
        logging.exception(f"Exception on /show/{show_id}")
        return "An error occurred: " + str(e), 500

@app.route('/search')
def search():
    """Handles show search requests."""
    try:
        query_text = request.args.get('query', '')
        if query_text:
            # Search in both name and episode title (case-insensitive)
            results = Show.query.filter(
                (Show.name.ilike(f'%{query_text}%')) | (Show.episode_title.ilike(f'%{query_text}%'))
            ).all()
        else:
            results = []
        return render_template('index.html', newly_added_shows=results, search_query=query_text)

    except Exception as e:
        logging.exception("Exception on /search")
        return "An error occurred: "+ str(e), 500

@app.route('/add_show', methods=['POST'])
def add_show_route():
    """Handles adding a new show via API (for Telegram bot)."""
    try:
        data = request.get_json()
        # Validate required fields
        if not all(key in data for key in ['name', 'episode_title', 'year', 'rating', 'genres', 'poster_url', 'telegram_message_id', 'overview', 'download_link']):
            return jsonify({'success': False, 'message': 'Missing required fields.'}), 400

        # Convert year and rating to appropriate types, handling potential errors
        try:
            year = int(data['year'])
            rating = float(data['rating'])
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid year or rating format.'}), 400

        # Check if the show already exists (based on Telegram message ID) to avoid duplicates
        existing_show = Show.query.filter_by(telegram_message_id=data['telegram_message_id']).first()
        if existing_show:
            return jsonify({'success': False, 'message': 'Show with this Telegram message ID already exists.'}), 409 # 409 Conflict

        # Create and add the new show
        new_show = Show(
            name=data['name'],
            episode_title=data['episode_title'],
            year=year,
            rating=rating,
            genres=data['genres'],  # Directly use the list
            poster_url=data['poster_url'],
            telegram_message_id=data['telegram_message_id'],
            overview=data['overview'],
            download_link=data['download_link']
        )
        db.session.add(new_show)
        db.session.commit()
        # Send a confirmation message (using Celery)
        send_telegram_message.delay(f"New show added:\n\n{data['name']} ({year})\nEpisode: {data['episode_title']}\n\nCheck it out on the site!")

        return jsonify({'success': True, 'message': 'Show added successfully!'}), 201 # 201 Created

    except Exception as e:
        db.session.rollback()  # Rollback in case of any error
        logging.exception("Exception on /add_show")
        return jsonify({'success': False, 'message': 'An error occurred: ' + str(e)}), 500
