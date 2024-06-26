import os
from datetime import timedelta
import logging
from logging.handlers import TimedRotatingFileHandler

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'partituras2024ubu')
    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/app/partituras/uploaded_sheets')
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    FIREBASE_CREDENTIALS_JSON_BASE64 = os.environ.get('FIREBASE_CREDENTIALS_JSON_BASE64', '')
    FIREBASE_BUCKET_NAME = os.environ.get('FIREBASE_BUCKET_NAME', 'sheet-transcribe.appspot.com')
    FIREBASE_CONFIG = {
        "apiKey": "AIzaSyDWnvC205VEIMrRhwqgRavndvfAeiGkGaY",
        "authDomain": "sheet-transcribe.firebaseapp.com",
        "projectId": "sheet-transcribe",
        "storageBucket": "sheet-transcribe.appspot.com",
        "messagingSenderId": "842930665850",
        "appId": "1:842930665850:web:ecb1dbf50a0583be36fec3",
        "measurementId": "G-06V85QSKVF",
        "databaseURL": ""
    }
    LOG_TO_STDOUT = os.environ.get('LOG_TO_STDOUT', 'true')

def configure_logging(app):
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = TimedRotatingFileHandler('logs/myapp.log', when='midnight')
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

    if app.config.get('LOG_TO_STDOUT'):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        app.logger.addHandler(stream_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info('Logging is set up.')
