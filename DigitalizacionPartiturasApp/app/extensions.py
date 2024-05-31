from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import firebase_admin
from firebase_admin import credentials, initialize_app, firestore, storage
import pyrebase
import logging

migrate = Migrate()
login_manager = LoginManager()

def init_firebase(app):
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(app.config['FIREBASE_CREDENTIALS'])
        initialize_app(cred, {'storageBucket': app.config['FIREBASE_BUCKET_NAME']})

    app.config['firestore_db'] = firestore.client()
    app.config['firebase_storage'] = storage.bucket(app.config['FIREBASE_BUCKET_NAME'])