from datetime import timedelta
from datetime import datetime
import json
import shutil
from flask import Blueprint, get_flashed_messages, render_template, request, redirect, send_from_directory, url_for, flash, session, current_app, send_file, Response
from werkzeug.utils import secure_filename
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import load_credentials_from_file, load_credentials_from_info
from .models import SheetMusic, User
from google.cloud import storage
import firebase_admin
from firebase_admin import credentials, initialize_app, firestore
import zipfile
import base64
import tempfile
import bcrypt
import subprocess
import os
import google.api_core.exceptions
from pdf2image import convert_from_path
import cv2
import numpy as np
import io
from os import listdir

main = Blueprint('main', __name__, template_folder='templates')

@main.route('/', methods=['GET'])
def index():
    return render_template('home.html')

@main.route('/home', methods=['GET'])
def home():
    return render_template('home.html')

@main.route('/login', methods=['GET', 'POST'])
def login():
    firestore_db = current_app.config.get('firestore_db')
    if not firestore_db:
        flash('Error de conexión', 'error')
        return render_template('login.html')

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user_ref = firestore_db.collection('users').document(username)
        user_snapshot = user_ref.get()

        if user_snapshot.exists:
            user_data = user_snapshot.to_dict()
            if bcrypt.checkpw(password.encode('utf-8'), user_data['passwordHash'].encode('utf-8')):
                user = User(username=user_data['username'], email=user_data['email'], passwordHash=user_data['passwordHash'])
                login_user(user, remember=False)
                next_page = request.args.get('next')
                return redirect(next_page or url_for('main.menu'))
            else:
                flash('Usuario o contraseña inválida', 'error')
        else:
            flash('Usuario no encontrado', 'error')

    return render_template('login.html')

@main.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('main.home'))

@main.route('/register', methods=['GET', 'POST'])
def register():
    firestore_db = current_app.config.get('firestore_db')
    if not firestore_db:
        flash('Error de conexión', 'error')
        return render_template('register.html')

    if request.method == 'POST':
        username = request.form['username']
        email = request.form.get('email')
        password = request.form['password']
        password2 = request.form['password2']

        if not email:
            flash('El email es obligatorio.', 'error')
            return render_template('register.html')

        if password != password2:
            flash('Las contraseñas no coinciden.', 'error')
            return render_template('register.html')

        user_ref = firestore_db.collection('users').document(username)
        user_snapshot = user_ref.get()
        if user_snapshot.exists:
            flash('El nombre de usuario ya existe.', 'error')
            return render_template('register.html')
        
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user_ref.set({
            'username': username,
            'email': email,
            'passwordHash': hashed_password
        })
        flash('El usuario se ha registrado correctamente', 'success')
        return redirect(url_for('main.login'))

    return render_template('register.html')

@main.route('/menu', methods=['GET'])
@login_required
def menu():
    return render_template('menu.html')

def digitalize_sheets(file_path, sheet_music):
    output_dir = '/app/audiveris_output'
    clean_directory(output_dir)
    
    batch_script = '/app/run_audiveris.sh'
    cmd = [batch_script, file_path]
        
    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            flash(f"Error durante la digitalización: {result.stderr}", 'error')
            return None, output_dir

        log_file = find_log_file(output_dir)
        if log_file is None:
            current_app.logger.error("No se encontró el archivo de log en el directorio: {}".format(output_dir))
            flash("No se encontró el archivo de log.", 'error')
            return None, output_dir

        is_valid, message = analyze_audiveris_log(log_file)
        if not is_valid:
            current_app.logger.error(f"Error en log de Audiveris: {message}")
            flash(message, 'error')
            return None, output_dir

        mxl_files = [file for file in os.listdir(output_dir) if file.endswith('.mxl')]
        current_app.logger.info(f"Archivos MXL encontrados: {mxl_files}")
        if not mxl_files:
            flash("Error de digitalización: No se encontró ningún archivo MXL.", 'error')
            return None, output_dir

        return os.path.join(output_dir, mxl_files[0]), output_dir

    except subprocess.CalledProcessError as e:
        flash(f"Error durante la digitalización: {e.stderr}", 'error')
        return None, output_dir
    except Exception as e:
        flash(f"Error al ejecutar Audiveris: {str(e)}", 'error')
        return None, output_dir


def find_log_file(directory):
    log_files = [f for f in os.listdir(directory) if f.endswith('.log')]
    if log_files:
        latest_log = max(log_files, key=lambda x: os.path.getmtime(os.path.join(directory, x)))
        return os.path.join(directory, latest_log)
    return None

def analyze_audiveris_log(log_file_path):
    try:
        with open(log_file_path, 'r') as file:
            log_contents = file.read()
        
        if "the picture resolution is too low" in log_contents:
            return False, "La resolución de la imagen es demasiado baja."
        if "this sheet contains no staves" in log_contents:
            return False, "La partitura no contiene pentagramas visibles."
        if "invalid sheet" in log_contents:
            return False, "Hoja inválida detectada."

        return True, "Digitalización completada sin errores detectados."

    except FileNotFoundError:
        return False, "Archivo de log no encontrado."
    except Exception as e:
        return False, f"Error al leer el archivo de log: {str(e)}"

def allowed_file(filename):
    return filename.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))

@main.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        file = request.files['file']
        title = request.form['title']
        if file and allowed_file(file.filename):
            original_filename = secure_filename(file.filename)
            extension = os.path.splitext(original_filename)[1]
            filename = secure_filename(title) + extension
            user_folder = f'user_{current_user.get_id()}/'
            file_type_folder = extension.replace('.', '') + '/'
            full_path = f'partituras/{user_folder}{file_type_folder}{filename}'
            blob = current_app.config['firebase_storage'].blob(full_path)
            blob.upload_from_file(file.stream, content_type=file.content_type)

            sheet_music = SheetMusic(title=title, file_path=full_path, upload_date=datetime.now())

            firestore_db = current_app.config['firestore_db']
            firestore_db.collection('sheet_music').document(filename).set({
                'title': sheet_music.title,
                'file_path': sheet_music.file_path,
                'upload_date': sheet_music.upload_date.isoformat()
            })

            flash('Partitura subida correctamente.')
            return redirect(url_for('main.menu'))

    return render_template('upload.html')

@main.route('/delete_sheet/<doc_id>', methods=['POST'])
@login_required
def delete_sheet(doc_id):
    try:
        firestore_db = current_app.config['firestore_db']
        doc_ref = firestore_db.collection('sheet_music').document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            file_details = doc.to_dict()
            file_path = file_details['file_path']

            blob = current_app.config['firebase_storage'].blob(file_path)
            blob.delete()

            doc_ref.delete()
            flash('Partitura eliminada correctamente.', 'success')
        else:
            flash('No se encontró la partitura especificada.', 'error')
    except Exception as e:
        current_app.logger.error(f"Error al eliminar la partitura: {str(e)}")
        flash(f'Error al eliminar la partitura: {str(e)}', 'error')

    return redirect(url_for('main.list_sheet_music'))

@main.route('/delete_digitalized_sheet/<doc_id>', methods=['POST'])
@login_required
def delete_digitalized_sheet(doc_id):
    try:
        firestore_db = current_app.config['firestore_db']
        doc_ref = firestore_db.collection('digitalized_sheets').document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            file_details = doc.to_dict()
            file_path = file_details['path']

            blob = current_app.config['firebase_storage'].blob(file_path)
            blob.delete()

            doc_ref.delete()
            flash('Partitura digitalizada eliminada correctamente.', 'success')
        else:
            flash('No se encontró la partitura digitalizada especificada.', 'error')
    except Exception as e:
        current_app.logger.error(f"Error al eliminar la partitura digitalizada: {str(e)}")
        flash(f'Error al eliminar la partitura digitalizada: {str(e)}', 'error')

    return redirect(url_for('main.list_digitalized_sheets'))


def download_sheets(bucket_name, file_name):
    filename_only = file_name.split('/')[-1]
    file_extension = filename_only.split('.')[-1].lower()
    
    file_type_folder = file_extension

    username = current_user.get_id()
    full_blob_name = f"partituras/user_{username}/{file_type_folder}/{filename_only}"
    current_app.logger.info(f"Descargando archivo desde Firebase: {full_blob_name}")

    try:
        firebase_credentials_base64 = os.environ.get('FIREBASE_CREDENTIALS_JSON_BASE64', '')
        if not firebase_credentials_base64:
            raise ValueError("La variable de entorno FIREBASE_CREDENTIALS_JSON_BASE64 no está configurada o está vacía.")
        
        cred_dict = json.loads(base64.b64decode(firebase_credentials_base64).decode('utf-8'))
        credentials, project = load_credentials_from_info(cred_dict)

        storage_client = storage.Client(credentials=credentials, project=project)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(full_blob_name)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_extension}')
        blob.download_to_filename(temp_file.name)

        current_app.logger.info(f'Archivo descargado: {temp_file.name}')
        return temp_file.name

    except google.api_core.exceptions.NotFound:
        current_app.logger.error(f"Archivo no encontrado: {full_blob_name}")
        flash("Archivo no encontrado.", 'error')
        return None
    except Exception as e:
        current_app.logger.error(f"Error al descargar el archivo: {str(e)}")
        flash("Error al descargar el archivo.", 'error')
        return None

@main.route('/digitalize_and_view/<path:filename>', methods=['GET'])
@login_required
def digitalize_and_view(filename):
    file_extension = filename.split('.')[-1]
    current_app.logger.info(f"Fichero en digitalize_and_view: {filename}")
    input_file_path = download_sheets(current_app.config['FIREBASE_BUCKET_NAME'], filename)

    if input_file_path is None:
        flash('No se pudo descargar el archivo para la digitalización.', 'error')
        return redirect(url_for('main.list_sheet_music'))

    if file_extension != 'mxl':
        title = filename.rsplit('.', 1)[0]
        sheet_music = SheetMusic(title=title, file_path=input_file_path)
        output_file_path, output_dir = digitalize_sheets(input_file_path, sheet_music)

        if output_file_path:
            try:
                user_folder = f'partituras/user_{current_user.get_id()}/mxl/'
                mxl_filename = secure_filename(sheet_music.title + '.mxl')
                full_path = f'{user_folder}{mxl_filename}'
                blob = current_app.config['firebase_storage'].blob(full_path)
                blob.upload_from_filename(output_file_path, content_type='application/vnd.recordare.musicxml+xml')

                firestore_db = current_app.config['firestore_db']
                firestore_db.collection('digitalized_sheets').document(mxl_filename).set({
                    'username': current_user.get_id(),
                    'filename': mxl_filename,
                    'path': full_path,
                    'uploaded_at': firestore.SERVER_TIMESTAMP
                })

                clean_directory(output_dir)

                flash('Partitura digitalizada correctamente.')
                return redirect(url_for('main.list_digitalized_sheets'))
            except Exception as e:
                flash('Error al subir el archivo digitalizado.', 'error')
                return redirect(url_for('main.list_sheet_music'))
        else:
            flash('No se pudo digitalizar la partitura.', 'error')
            flash('Preprocesa la imagen antes de digitalizarla.')
            return redirect(url_for('main.list_sheet_music'))
    else:
        return redirect(url_for('main.view_sheet', filename=filename))
    
def clean_directory(directory_path):
    try:
        for file_name in os.listdir(directory_path):
            file_path = os.path.join(directory_path, file_name)
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        current_app.logger.info("Directorio limpiado completamente")
    except Exception as e:
        current_app.logger.error(f"No se pudo limpiar el directorio. Error: {str(e)}")

@main.route('/list_sheet_music', methods=['GET'])
@login_required
def list_sheet_music():
    try:
        user_folder = f'partituras/user_{current_user.get_id()}/'
        bucket = current_app.config['firebase_storage']
        
        sheet_music_files = []

        blobs = bucket.list_blobs(prefix=user_folder)
        for blob in blobs:
            file_extension = blob.name.split('.')[-1]
            if file_extension not in ['mxl']:
                if allowed_file(blob.name):
                    file_name = os.path.basename(blob.name)
                    file_type_folder = file_extension if file_extension in ['pdf', 'jpg', 'png', 'jpeg'] else 'images'
                    download_url = url_for('main.digitalize_and_view', filename=f"{file_type_folder}/{file_name}")
                    
                    firestore_db = current_app.config['firestore_db']
                    doc_ref = firestore_db.collection('sheet_music').document(file_name)
                    doc_id = doc_ref.id

                    sheet_music_files.append({'name': file_name, 'url': download_url, 'doc_id': doc_id})
        
    except Exception as e:
        current_app.logger.error(f"Error al listar las partituras: {str(e)}")
        flash(f"Error al listar las partituras: {str(e)}")

    return render_template('list_sheet_music.html', sheet_music_files=sheet_music_files)


@main.route('/view_sheet/<filename>', methods=['GET'])
@login_required
def view_sheet(filename):
    user_folder = f'partituras/user_{current_user.get_id()}/mxl/'
    bucket = current_app.config['firebase_storage']
    full_blob_name = f"{user_folder}{filename}"
    
    try:
        if not firebase_admin._apps:
            cred_dict = json.loads(base64.b64decode(current_app.config['FIREBASE_CREDENTIALS_JSON_BASE64']).decode('utf-8'))
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        else:
            cred = firebase_admin.get_app().credential.get_credential()

        storage_client = storage.Client(credentials=cred, project=cred.project_id)
        bucket = storage_client.bucket(current_app.config['FIREBASE_BUCKET_NAME'])
        blob = bucket.blob(full_blob_name)

        mxl_bytes = blob.download_as_bytes()
        
        with zipfile.ZipFile(io.BytesIO(mxl_bytes)) as z:
            for file in z.namelist():
                if file.endswith('.xml'):
                    with z.open(file) as xml_file:
                        xml_content = xml_file.read()
                        break
        
        xml_content_b64 = base64.b64encode(xml_content).decode('utf-8')

        return render_template('view_sheet.html', mxl_content=xml_content_b64)
    except google.api_core.exceptions.NotFound:
        flash('El archivo solicitado no existe.', 'error')
        return redirect(url_for('main.list_sheet_music'))
    except Exception as e:
        flash('Error al descargar la partitura digitalizada.', 'error')
        return redirect(url_for('main.list_sheet_music'))

@main.route('/list_digitalized_sheets', methods=['GET'])
@login_required
def list_digitalized_sheets():
    try:
        firestore_db = current_app.config['firestore_db']
        bucket = current_app.config['firebase_storage']
        sheets = firestore_db.collection('digitalized_sheets').where('username', '==', current_user.get_id()).stream()

        sheet_music_files = []
        for sheet in sheets:
            sheet_data = sheet.to_dict()
            full_blob_name = f"partituras/user_{current_user.get_id()}/mxl/{sheet_data['filename']}"

            blob = bucket.blob(full_blob_name)
            if blob.exists():
                doc_id = sheet.id
                sheet_music_files.append({'name': sheet_data['filename'], 'url': url_for('main.view_sheet', filename=sheet_data['filename']), 'doc_id': doc_id})
            else:
                firestore_db.collection('digitalized_sheets').document(sheet.id).delete()

    except Exception as e:
        flash(f"Error al listar las partituras digitalizadas: {str(e)}")

    return render_template('list_digitalized_sheets.html', sheet_music_files=sheet_music_files)

def preprocess_image(file_path, median_kernel_size, adaptive_threshold_block_size, adaptive_threshold_c, erosion_iterations, dilation_iterations):
    try:
        if file_path.lower().endswith('.pdf'):
            images = convert_from_path(file_path)
            if not images:
                raise FileNotFoundError("No se pudieron convertir las páginas del PDF a imágenes.")
            image = images[0]
            img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        else:
            img = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"No se pudo cargar la imagen desde la ruta: {file_path}")
        
        img = cv2.medianBlur(img, median_kernel_size)
        img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, adaptive_threshold_block_size, adaptive_threshold_c)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        
        img = cv2.erode(img, kernel, iterations=erosion_iterations)
        img = cv2.dilate(img, kernel, iterations=dilation_iterations)
        
        processed_file_path = file_path.replace(".png", "_processed.png").replace(".pdf", "_processed.png").replace(".jpg", "_processed.jpg").replace(".jpeg", "_processed.jpeg")
        cv2.imwrite(processed_file_path, img)
        return processed_file_path
    except Exception as e:
        raise e

@main.route('/preprocess/<path:filename>', methods=['GET', 'POST'])
@login_required
def preprocess(filename):
    if request.method == 'POST':
        try:
            median_kernel_size = request.form.get('median_kernel_size', 5, type=int)
            adaptive_threshold_block_size = request.form.get('adaptive_threshold_block_size', 11, type=int)
            adaptive_threshold_c = request.form.get('adaptive_threshold_c', 2, type=int)
            erosion_iterations = request.form.get('erosion_iterations', 1, type=int)
            dilation_iterations = request.form.get('dilation_iterations', 1, type=int)

            input_file_path = download_sheets(current_app.config['FIREBASE_BUCKET_NAME'], filename)
            if input_file_path is None:
                flash('No se pudo descargar el archivo para el preprocesamiento.', 'error')
                current_app.logger.error("Archivo no encontrado en Firebase.")
                return redirect(url_for('main.list_sheet_music'))

            processed_file_path = preprocess_image(input_file_path, median_kernel_size, adaptive_threshold_block_size, adaptive_threshold_c, erosion_iterations, dilation_iterations)
            
            return send_file(processed_file_path, as_attachment=True)
        except FileNotFoundError as e:
            flash('Error al cargar la imagen para el preprocesamiento.', 'error')
            current_app.logger.error(f'FileNotFoundError: {e}')
            return redirect(url_for('main.list_sheet_music'))
        except Exception as e:
            flash('Error al procesar la imagen.', 'error')
            current_app.logger.error(f'Exception: {e}')
            return redirect(url_for('main.list_sheet_music'))
    else:
        return render_template('preprocess_form_page.html', filename=filename)

def configure_routes(app):
    app.register_blueprint(main)