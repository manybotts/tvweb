# tv_app/routes.py
from flask import render_template, request, jsonify, redirect, url_for
from .models import db, TVShow  # Import db and your model(s)
from .app import create_app  # Import create_app
from flask import current_app as app

@app.route('/')
def index():
    shows = TVShow.query.all()
    return render_template('index.html', shows=shows)

@app.route('/show/<int:message_id>')
def show_detail(message_id):
    show = TVShow.query.filter_by(message_id=message_id).first_or_404()
    return render_template('show_detail.html', show=show)

@app.route('/latest')
def latest_shows():
    shows = TVShow.query.order_by(TVShow.id.desc()).limit(10).all()  # Get last 10 shows
    return render_template('index.html', shows=shows) # You might want a different template

@app.route('/delete/<int:message_id>', methods=['POST'])
def delete_show(message_id):
    show = TVShow.query.get_or_404(message_id)
    db.session.delete(show)
    db.session.commit()
    return redirect(url_for('index'))  # Redirect to the index page after deletion

# Example API endpoint (returning JSON)
@app.route('/api/shows')
def api_shows():
    shows = TVShow.query.all()
    show_list = []
    for show in shows:
        show_list.append({
            'id': show.id,
            'message_id': show.message_id,
            'show_name': show.show_name,
            'episode_title': show.episode_title,
            'download_link': show.download_link,
            'overview' : show.overview,
            'poster_path': show.poster_path,
            'vote_average': show.vote_average
        })
    return jsonify(show_list)
