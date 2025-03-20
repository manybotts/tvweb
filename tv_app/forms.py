# tv_app/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, TextAreaField, IntegerField, SelectField
from wtforms.validators import DataRequired, Length, Optional, URL, InputRequired

class AdminLoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=80)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class AddShowForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=255)])
    overview = TextAreaField('Overview', validators=[Optional()])
    release_year = IntegerField('Release Year', validators=[Optional()])
    genre = StringField('Genre', validators=[Optional(), Length(max=255)])
    image_url = StringField('Image URL', validators=[Optional(), URL(), Length(max=255)])
    trailer_url = StringField('Trailer URL', validators=[Optional(), URL(), Length(max=255)])
    imdb_id = StringField('IMDB ID', validators=[Optional(), Length(max=255)])
    download_link = StringField('Download Link', validators=[Optional(), Length(max=255)]) #General show link
    available_seasons = IntegerField('Available Seasons', validators=[Optional()])
    is_new = BooleanField('Is New')
    on_slider = BooleanField('On Slider')
    submit_show = SubmitField('Add Show')


class AddEpisodeForm(FlaskForm):
    title = StringField('Episode Title', validators=[Optional(), Length(max=255)]) #Optional title
    episode_number = IntegerField('Episode Number', validators=[InputRequired()])
    season_number = IntegerField('Season Number', validators=[InputRequired()])
    show_id = SelectField('Show', coerce=int, validators=[InputRequired()])
    download_link = StringField('Download Link', validators=[DataRequired(), Length(max=255)])
    overview = TextAreaField('Episode Overview', validators=[Optional()])
    submit_episode = SubmitField('Add Episode')
