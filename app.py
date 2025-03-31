import os
import csv
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# Choose database URI based on environment.
if os.environ.get("USE_INTERNAL_DB", "false").lower() == "true":
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('INTERNAL_DATABASE_URL')
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('EXTERNAL_DATABASE_URL')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Use engine options to recycle connections and pre-ping them.
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,
    'pool_pre_ping': True
}

# Configure upload folders (Note: Uploaded files may be ephemeral on Render)
app.config['MEMBER_UPLOAD_FOLDER'] = os.path.join('static', 'uploads', 'members')
app.config['DOCUMENT_UPLOAD_FOLDER'] = os.path.join('static', 'uploads', 'documents')
os.makedirs(app.config['MEMBER_UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOCUMENT_UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Initialize Flask-Migrate
from flask_migrate import Migrate
migrate = Migrate(app, db)

# ----------------------
# Database Models
# ----------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    # Email field removed.
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # 'main_admin' or 'admin'
    approved = db.Column(db.Boolean, default=False)   # Non-main admin require approval

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    barcode = db.Column(db.String(100), unique=True, nullable=False)
    department = db.Column(db.String(150))
    assembly = db.Column(db.String(150))
    picture = db.Column(db.String(150))
    entry_type = db.Column(db.String(50))
    entry_year = db.Column(db.String(4))
    date_of_birth = db.Column(db.Date)
    category = db.Column(db.String(20))  # 'ADULT', 'YOUTH', or 'CHILDREN'

class Folder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('folder.id'), nullable=True)
    children = db.relationship('Folder', backref=db.backref('parent', remote_side=[id]), lazy=True)

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(150), nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey('folder.id'), nullable=True)
    folder = db.relationship('Folder', backref=db.backref('documents', lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ----------------------
# Routes for Serving Uploaded Files
# ----------------------
@app.route('/uploads/members/<filename>', endpoint='uploaded_member')
def uploaded_member(filename):
    return send_from_directory(app.config['MEMBER_UPLOAD_FOLDER'], filename)

@app.route('/uploads/documents/<filename>', endpoint='uploaded_document')
def uploaded_document(filename):
    return send_from_directory(app.config['DOCUMENT_UPLOAD_FOLDER'], filename)

# ----------------------
# Authentication Routes
# ----------------------
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Login requires only username and password.
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user:
            if user.role != 'main_admin' and not user.approved:
                flash('Your account is pending approval by the main admin.', 'danger')
                return redirect(url_for('login'))
            if user.check_password(password):
                login_user(user)
                flash(f'Welcome, {user.username}!', 'success')
                return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # Signup requires username, password, and role.
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        approved = True if not User.query.first() or role == 'main_admin' else False
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('signup'))
        new_user = User(username=username, role=role, approved=approved)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        if approved:
            flash('Account created successfully', 'success')
            return redirect(url_for('login'))
        else:
            flash('Your account is pending approval by the main admin.', 'info')
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

# ----------------------
# Dashboard with Real-Time Date/Time
# ----------------------
@app.route('/dashboard')
@login_required
def dashboard():
    adult_count = Member.query.filter_by(category='ADULT').count()
    youth_count = Member.query.filter_by(category='YOUTH').count()
    children_count = Member.query.filter_by(category='CHILDREN').count()
    return render_template('dashboard.html',
                           adult_count=adult_count,
                           youth_count=youth_count,
                           children_count=children_count)

# ----------------------
# Member Management
# ----------------------
@app.route('/members/<category>', methods=['GET'])
@login_required
def list_members(category):
    query = Member.query.filter_by(category=category.upper())
    search_term = request.args.get('search')
    sort_order = request.args.get('sort', 'asc')
    if search_term:
        query = query.filter(
            Member.full_name.contains(search_term) |
            Member.barcode.contains(search_term) |
            Member.department.contains(search_term)
        )
    if sort_order == 'name_asc':
        query = query.order_by(Member.full_name.asc())
    elif sort_order == 'name_desc':
        query = query.order_by(Member.full_name.desc())
    elif sort_order == 'dept_asc':
        query = query.order_by(Member.department.asc())
    elif sort_order == 'dept_desc':
        query = query.order_by(Member.department.desc())
    elif sort_order == 'barcode_asc':
        query = query.order_by(Member.barcode.asc())
    elif sort_order == 'barcode_desc':
        query = query.order_by(Member.barcode.desc())
    else:
        query = query.order_by(Member.full_name.asc())
    members = query.all()
    return render_template(f"{category.lower()}_members.html",
                           members=members,
                           category=category,
                           sort_order=sort_order)

@app.route('/member/add', methods=['GET', 'POST'])
@login_required
def add_member():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        if not full_name:
            flash('Full Name is required.', 'danger')
            return redirect(url_for('add_member'))
        barcode = request.form.get('barcode')
        department = request.form.get('department')
        assembly = request.form.get('assembly')
        entry_type = request.form.get('entry_type')
        entry_year = request.form.get('entry_year')
        dob_str = request.form.get('date_of_birth')
        dob = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None
        category = request.form.get('category')
        picture = request.files.get('picture')
        filename = None
        if picture and picture.filename != '':
            ext = os.path.splitext(picture.filename)[1]
            filename = secure_filename(barcode + ext)
            picture.save(os.path.join(app.config['MEMBER_UPLOAD_FOLDER'], filename))
        new_member = Member(
            full_name=full_name,
            barcode=barcode,
            department=department,
            assembly=assembly,
            entry_type=entry_type,
            entry_year=entry_year,
            date_of_birth=dob,
            category=category.upper(),
            picture=filename
        )
        db.session.add(new_member)
        db.session.commit()
        flash('Member added successfully', 'success')
        return redirect(url_for('dashboard'))
    return render_template('member_form.html', action='Add')

@app.route('/member/edit/<int:member_id>', methods=['GET', 'POST'])
@login_required
def edit_member(member_id):
    member = Member.query.get_or_404(member_id)
    if request.method == 'POST':
        member.full_name = request.form.get('full_name')
        member.barcode = request.form.get('barcode')
        member.department = request.form.get('department')
        member.assembly = request.form.get('assembly')
        member.entry_type = request.form.get('entry_type')
        member.entry_year = request.form.get('entry_year')
        dob_str = request.form.get('date_of_birth')
        member.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None
        member.category = request.form.get('category').upper()
        picture = request.files.get('picture')
        if picture and picture.filename != '':
            ext = os.path.splitext(picture.filename)[1]
            filename = secure_filename(member.barcode + ext)
            picture.save(os.path.join(app.config['MEMBER_UPLOAD_FOLDER'], filename))
            member.picture = filename
        db.session.commit()
        flash('Member updated successfully', 'success')
        return redirect(url_for('dashboard'))
    return render_template('member_form.html', action='Edit', member=member)

@app.route('/member/delete/<int:member_id>', methods=['POST'])
@login_required
def delete_member(member_id):
    member = Member.query.get_or_404(member_id)
    db.session.delete(member)
    db.session.commit()
    flash('Member deleted successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/member/<int:member_id>')
@login_required
def member_details(member_id):
    member = Member.query.get_or_404(member_id)
    return render_template('member_details.html', member=member)

@app.route('/member/scan', methods=['GET', 'POST'])
@login_required
def scan_member():
    member = None
    if request.method == 'POST':
        barcode = request.form.get('barcode')
        member = Member.query.filter_by(barcode=barcode).first()
        if not member:
            flash('Member not found', 'danger')
    return render_template('scan_member.html', member=member)

# ----------------------
# Export Feature
# ----------------------
@app.route('/export', methods=['GET', 'POST'])
@login_required
def export():
    if request.method == 'POST':
        export_type = request.form.get('export_type')
        if export_type == 'members':
            category = request.form.get('member_category')
            selected_columns = request.form.getlist('member_columns')
            query = Member.query.filter_by(category=category.upper())
            members = query.all()
            def generate_members():
                yield ','.join(selected_columns) + "\n"
                for m in members:
                    row = []
                    for col in selected_columns:
                        if col == 'ID':
                            row.append(str(m.id))
                        elif col == 'Full Name':
                            row.append(m.full_name)
                        elif col == 'Barcode':
                            row.append(m.barcode)
                        elif col == 'Department':
                            row.append(m.department or '')
                        elif col == 'Assembly':
                            row.append(m.assembly or '')
                        elif col == 'Entry Type':
                            row.append(m.entry_type or '')
                        elif col == 'Entry Year':
                            row.append(m.entry_year or '')
                        elif col == 'Date of Birth':
                            row.append(m.date_of_birth.strftime('%Y-%m-%d') if m.date_of_birth else '')
                        elif col == 'Category':
                            row.append(m.category)
                        else:
                            row.append('')
                    yield ','.join(row) + "\n"
            return Response(generate_members(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=members_export.csv"})
        elif export_type == 'programs':
            folder_id = request.form.get('folder_id')
            selected_columns = request.form.getlist('doc_columns')
            documents = Document.query.filter_by(folder_id=folder_id).all() if folder_id else Document.query.all()
            def generate_documents():
                yield ','.join(selected_columns) + "\n"
                for doc in documents:
                    row = []
                    for col in selected_columns:
                        if col == 'Document ID':
                            row.append(str(doc.id))
                        elif col == 'Filename':
                            row.append(doc.filename)
                        elif col == 'Folder ID':
                            row.append(str(doc.folder_id) if doc.folder_id else '')
                        else:
                            row.append('')
                    yield ','.join(row) + "\n"
            return Response(generate_documents(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=documents_export.csv"})
    member_columns = ['ID', 'Full Name', 'Barcode', 'Department', 'Assembly', 'Entry Type', 'Entry Year', 'Date of Birth', 'Category']
    doc_columns = ['Document ID', 'Filename', 'Folder ID']
    folders_list = Folder.query.filter_by(parent_id=None).all()
    return render_template('export.html', member_columns=member_columns, doc_columns=doc_columns, folders=folders_list)

# ----------------------
# Programs & Messages
# ----------------------
@app.route('/programs_messages', methods=['GET'])
@login_required
def programs_messages():
    main_folders = Folder.query.filter_by(parent_id=None).all()
    return render_template('programs_messages.html', folders=main_folders)

@app.route('/folder/view/<int:folder_id>', methods=['GET'])
@login_required
def view_folder(folder_id):
    folder = Folder.query.get_or_404(folder_id)
    subfolders = Folder.query.filter_by(parent_id=folder.id).all()
    documents = Document.query.filter_by(folder_id=folder.id).all()
    return render_template('view_folder.html', folder=folder, subfolders=subfolders, documents=documents)

# ----------------------
# Folders Management
# ----------------------
@app.route('/folders', methods=['GET'])
@login_required
def folders_page():
    search_term = request.args.get('search')
    if search_term:
        folders = Folder.query.filter(Folder.name.contains(search_term)).all()
        documents = Document.query.filter(Document.filename.contains(search_term)).all()
    else:
        folders = Folder.query.filter_by(parent_id=None).all()
        documents = Document.query.all()
    return render_template('folders.html', folders=folders, documents=documents, search=search_term)

@app.route('/folder/create', methods=['GET', 'POST'])
@login_required
def create_folder():
    if request.method == 'POST':
        folder_name = request.form.get('folder_name')
        parent_id = request.form.get('parent_id')
        new_folder = Folder(name=folder_name, parent_id=parent_id if parent_id != '' else None)
        db.session.add(new_folder)
        db.session.commit()
        flash('Folder created successfully', 'success')
        return redirect(url_for('folders_page'))
    folders_all = Folder.query.all()
    return render_template('create_folder.html', folders=folders_all)

@app.route('/folder/rename/<int:folder_id>', methods=['GET', 'POST'])
@login_required
def rename_folder(folder_id):
    folder = Folder.query.get_or_404(folder_id)
    if request.method == 'POST':
        new_name = request.form.get('new_name')
        if new_name:
            folder.name = new_name
            db.session.commit()
            flash('Folder renamed successfully', 'success')
            return redirect(url_for('folders_page'))
        else:
            flash('New name cannot be empty', 'danger')
    return render_template('rename_folder.html', folder=folder)

@app.route('/folder/delete/<int:folder_id>', methods=['POST'])
@login_required
def delete_folder(folder_id):
    folder = Folder.query.get_or_404(folder_id)
    db.session.delete(folder)
    db.session.commit()
    flash('Folder deleted successfully', 'success')
    return redirect(url_for('folders_page'))

@app.route('/folders/upload', methods=['GET', 'POST'])
@login_required
def upload_file():
    if request.method == 'POST':
        folder_id = request.form.get('folder_id')
        file = request.files.get('document')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['DOCUMENT_UPLOAD_FOLDER'], filename))
            new_doc = Document(filename=filename, folder_id=folder_id if folder_id != '' else None)
            db.session.add(new_doc)
            db.session.commit()
            flash('Document uploaded successfully', 'success')
            return redirect(url_for('folders_page'))
        else:
            flash('No file selected', 'danger')
    folders_all = Folder.query.all()
    return render_template('upload_document.html', folders=folders_all)

@app.route('/document/rename/<int:doc_id>', methods=['GET', 'POST'])
@login_required
def rename_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    if request.method == 'POST':
        new_name = request.form.get('new_name')
        if new_name:
            old_filename = doc.filename
            ext = os.path.splitext(old_filename)[1]
            new_filename = secure_filename(new_name + ext)
            old_path = os.path.join(app.config['DOCUMENT_UPLOAD_FOLDER'], old_filename)
            new_path = os.path.join(app.config['DOCUMENT_UPLOAD_FOLDER'], new_filename)
            try:
                os.rename(old_path, new_path)
            except Exception as e:
                flash(f'Error renaming file: {e}', 'danger')
                return redirect(url_for('folders_page'))
            doc.filename = new_filename
            db.session.commit()
            flash('Document renamed successfully', 'success')
            return redirect(url_for('folders_page'))
        else:
            flash('New name cannot be empty', 'danger')
    return render_template('rename_document.html', document=doc)

@app.route('/document/delete/<int:doc_id>', methods=['POST'])
@login_required
def delete_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    file_path = os.path.join(app.config['DOCUMENT_UPLOAD_FOLDER'], doc.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(doc)
    db.session.commit()
    flash('Document deleted successfully', 'success')
    return redirect(url_for('folders_page'))

@app.route('/document/view/<int:doc_id>')
@login_required
def view_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    ext = os.path.splitext(doc.filename)[1].lower()
    if ext in ['.txt', '.py', '.md', '.log']:
        try:
            with open(os.path.join(app.config['DOCUMENT_UPLOAD_FOLDER'], doc.filename), 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            flash(f'Error reading file: {e}', 'danger')
            content = ''
        return render_template('view_document.html', document=doc, content=content)
    else:
        return send_from_directory(app.config['DOCUMENT_UPLOAD_FOLDER'], doc.filename)

# ----------------------
# Admin Management & Approval
# ----------------------
@app.route('/assign_roles', methods=['GET', 'POST'])
@login_required
def assign_roles():
    if current_user.role != 'main_admin':
        flash('Only main admin can manage user roles', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        new_role = request.form.get('role')
        user = User.query.get(user_id)
        if user:
            user.role = new_role
            db.session.commit()
            flash('User role updated', 'success')
        return redirect(url_for('assign_roles'))
    return render_template('assign_roles.html', users=users)

@app.route('/approve_admins', methods=['GET', 'POST'])
@login_required
def approve_admins():
    if current_user.role != 'main_admin':
        flash('Only main admin can approve admin signups', 'danger')
        return redirect(url_for('dashboard'))
    pending_users = User.query.filter_by(approved=False).all()
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        user = User.query.get(user_id)
        if user:
            user.approved = True
            db.session.commit()
            flash(f'User {user.username} approved', 'success')
        return redirect(url_for('approve_admins'))
    return render_template('approve_admins.html', pending_users=pending_users)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'main_admin':
        flash('Only main admin can delete users', 'danger')
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account', 'danger')
        return redirect(url_for('assign_roles'))
    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully', 'success')
    return redirect(url_for('assign_roles'))

# ----------------------
# Run the Application
# ----------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=port, debug=True)
