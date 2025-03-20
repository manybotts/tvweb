
# tv_app/init_db.py
# tv_app/__init__.py
# This file can remain relatively simple.  We're *not* using the
# application factory pattern here.
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os

load_dotenv()

db = SQLAlchemy()
