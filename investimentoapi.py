import os
import time
import random
import jwt
import threading
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURAÇÃO CORE ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Magia do Banco de Dados: Se tiver URL do Render, usa Postgres. Se não, usa SQLite local pra teste.
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v2.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "chave_secreta_ninja_god_mode")

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- MODELOS DE BANCO DE DADOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_status = db.Column(db.String(20), default='normal')
    role = db.Column(db.String(20), default='user') # 'user' ou 'admin'

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)

class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount = db.Column(db.Float, nullable=False)
    pix_key = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default='pendente') # pendente, aprovado, rejeitado
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class InvestmentPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    yield_total = db.Column(db.Float, nullable=False)
    days = db.Column(db.Integer, nullable=False)
    min_amount = db.Column(db.Float, nullable=False)

# --- INICIALIZAÇÃO DO BANCO ---
with app.app_context():
    db.create_all()
    # Cria o Admin God Mode se não existir
    if not User.query.filter_by(role='admin').first():
        hashed_pw = generate_password_hash("admin")
        admin = User(username="DeusNexus", email="admin@nexus.com", password_hash=hashed_pw, role='admin')
        db.session.add(admin)
    
    # Cria configurações padrão dos jogos se não existirem
    default_configs = {
        'mines_house_edge': '50',
        'double_white_chance': '5.0',
        'aviator_max_mult': '50.0'
    }
    for k, v in default_configs.items():
        if not Config.query.filter_by(key=k).first():
            db.session.add(Config(key=k, value=v))
    
    db.session.commit()

# --- MIDDLEWARES (PROTEÇÃO JWT) ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token: return jsonify({'success': False, 'msg': 'Token ausente!'}), 401
        try:
            token = token.split(" ")[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user: raise Exception()
        except:
            return jsonify({'success': False, 'msg': 'Token inválido!'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token: return jsonify({'success': False, 'msg': 'Sem permissão!'}), 403
        try:
            token = token.split(" ")[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user or current_user.role != 'admin': raise Exception()
        except:
            return jsonify({'success': False, 'msg': 'Acesso negado. Apenas Admins.'}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter((User.email == data.get('login')) | (User.username == data.get('login'))).first()
    
    if user and check_password_hash(user.password_hash, data.get('password')):
        token = jwt.encode({'user_id': user.id, 'exp': datetime.utcnow() + timedelta(days=7)}, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'success': True, 'token': token, 'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role, 'vip': user.vip_status}})
    return jsonify({'success': False, 'msg': 'Login ou senha incorretos'}), 401

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(email=data.get('email')).first() or User.query.filter_by(username=data.get('username')).first():
        return jsonify({'success': False, 'msg': 'Usuário ou Email já existe!'}), 400

    new_user = User(
        username=data.get('username'),
        email=data.get('email'),
        password_hash=generate_password_hash(data.get('password'))
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada com sucesso!'})


# --- ROTAS DO PAINEL ADMIN (GOD MODE) ---
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def get_dashboard_data(current_user):
    users = User.query.filter_by(role='user').all()
    withdrawals = Withdrawal.query.filter_by(status='pendente').all()
    configs = Config.query.all()
    plans = InvestmentPlan.query.all()
    
    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'username': u.username, 'balance': u.balance, 'vip': u.vip_status} for u in users],
        'withdrawals': [{'id': w.id, 'user_id': w.user_id, 'amount': w.amount, 'pix': w.pix_key, 'date': w.created_at.strftime("%d/%m/%Y %H:%M")} for w in withdrawals],
        'configs': {c.key: c.value for c in configs},
        'plans': [{'id': p.id, 'name': p.name, 'yieldTotal': p.yield_total, 'days': p.days, 'min': p.min_amount} for p in plans]
    })

@app.route('/api/admin/config_games', methods=['POST'])
@admin_required
def update_game_configs(current_user):
    data = request.json
    # Atualiza as chances do banco
    if 'mines_edge' in data: Config.query.filter_by(key='mines_house_edge').first().value = str(data['mines_edge'])
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Algoritmos atualizados!'})

@app.route('/api/admin/user/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(current_user, user_id):
    user = User.query.get(user_id)
    if user and user.role != 'admin':
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


# --- WEBSOCKETS (JOGOS AO VIVO) ---
def double_game_loop():
    while True:
        socketio.emit('double_status', {'state': 'waiting', 'time': 10})
        time.sleep(10)
        socketio.emit('double_status', {'state': 'spinning'})
        
        # Aqui o backend poderia ler o `double_white_chance` do banco
        r = random.random()
        res = 'white' if r < 0.05 else ('red' if r < 0.525 else 'black')
        
        time.sleep(4)
        socketio.emit('double_result', {'color': res})
        time.sleep(4)

def aviator_game_loop():
    while True:
        socketio.emit('aviator_status', {'state': 'waiting', 'time': 5})
        time.sleep(5)
        socketio.emit('aviator_status', {'state': 'flying'})
        
        crash_point = round(random.uniform(1.01, 15.0), 2)
        current_mult = 1.00
        while current_mult < crash_point:
            time.sleep(0.1)
            current_mult += 0.01 * (current_mult * 0.5)
            if current_mult >= crash_point: current_mult = crash_point
            socketio.emit('aviator_tick', {'multiplier': round(current_mult, 2)})
            
        socketio.emit('aviator_crash', {'crash_at': crash_point})
        time.sleep(3)

# Inicia Motores de Jogo
threading.Thread(target=double_game_loop, daemon=True).start()
threading.Thread(target=aviator_game_loop, daemon=True).start()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
