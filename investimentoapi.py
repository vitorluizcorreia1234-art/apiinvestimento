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
import mercadopago

# --- CONFIGURAÇÃO CORE ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração de Banco de Dados (Pronto para o Render com PostgreSQL)
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v2.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "super_chave_ninja_10x")

db = SQLAlchemy(app)

# WebSockets para Tempo Real (Sincronia entre todos os players)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Integração Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "SEU_TOKEN_AQUI")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


# --- MODELOS DE BANCO DE DADOS (Mais Seguros) ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    balance = db.Column(db.Float, default=0.0)
    role = db.Column(db.String(20), default='user')  # 'user' ou 'admin'
    is_active = db.Column(db.Boolean, default=True)


class GameHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game = db.Column(db.String(50), nullable=False)  # 'double', 'aviator'
    result = db.Column(db.String(50), nullable=False)  # 'red', '2.5x'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Criação das tabelas
with app.app_context():
    db.create_all()
    # Criar admin padrão se não existir
    if not User.query.filter_by(role='admin').first():
        hashed_pw = generate_password_hash("Admin@10xNinja")
        admin = User(username="SystemAdmin", email="admin@nexus.com", password_hash=hashed_pw, role='admin')
        db.session.add(admin)
        db.session.commit()


# --- MIDDLEWARE DE SEGURANÇA (JWT) ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'msg': 'Token ausente!'}), 401
        try:
            token = token.split(" ")[1]  # Remove o 'Bearer '
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
        except:
            return jsonify({'success': False, 'msg': 'Token inválido ou expirado!'}), 401
        return f(current_user, *args, **kwargs)

    return decorated


# --- ROTAS DE AUTENTICAÇÃO (Melhoradas) ---
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter((User.email == data.get('login')) | (User.username == data.get('login'))).first()

    if user and check_password_hash(user.password_hash, data.get('password')):
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.utcnow() + timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")

        return jsonify({
            'success': True,
            'token': token,
            'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role}
        })
    return jsonify({'success': False, 'msg': 'Credenciais incorretas'}), 401


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(email=data.get('email')).first():
        return jsonify({'success': False, 'msg': 'Email já cadastrado'}), 400

    hashed_pw = generate_password_hash(data.get('password'))
    new_user = User(
        username=data.get('username'),
        email=data.get('email'),
        phone=data.get('phone'),
        password_hash=hashed_pw
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta ninja criada com sucesso!'})


# --- SISTEMA DE BOTS E LISTA AO VIVO ---
NOME_BOTS = ["GamerBR", "Ninja99", "TraderPro", "Rico2026", "Sniper", "LoboWallSt", "AlphaX"]


def generate_live_bets(game_name):
    # Gera uma lista de 3 a 5 bots apostando valores aleatórios
    bets = []
    for _ in range(random.randint(3, 5)):
        bets.append({
            'user': random.choice(NOME_BOTS),
            'amount': round(random.uniform(10.0, 500.0), 2),
            'is_bot': True
        })
    # Ordena pelos maiores valores (Top 5)
    return sorted(bets, key=lambda x: x['amount'], reverse=True)


# --- MOTORES DE JOGO EM TEMPO REAL (WEBSOCKETS) ---
# Essas funções rodam em background e sincronizam TODOS os players conectados

def double_game_loop():
    while True:
        # Fase 1: Apostas Abertas
        socketio.emit('double_status', {'state': 'waiting', 'time': 10})
        live_bets = generate_live_bets('double')
        socketio.emit('live_bets_update', live_bets)
        time.sleep(10)

        # Fase 2: Girando
        socketio.emit('double_status', {'state': 'spinning'})

        # Lógica matemática (Configurável via painel no futuro)
        r = random.random()
        if r < 0.05:
            result_color = 'white'
        elif r < 0.525:
            result_color = 'red'
        else:
            result_color = 'black'

        time.sleep(4)  # Tempo da animação no frontend

        # Fase 3: Resultado
        with app.app_context():
            # Salvar no histórico global
            history = GameHistory(game='double', result=result_color)
            db.session.add(history)
            db.session.commit()

            # Puxar os últimos 10 resultados para mandar pra tela
            last_results = GameHistory.query.filter_by(game='double').order_by(GameHistory.id.desc()).limit(10).all()
            history_list = [h.result for h in last_results]

        socketio.emit('double_result', {'color': result_color, 'history': history_list})
        time.sleep(4)  # Tempo mostrando quem ganhou


def aviator_game_loop():
    while True:
        socketio.emit('aviator_status', {'state': 'waiting', 'time': 5})
        live_bets = generate_live_bets('aviator')
        socketio.emit('live_bets_update', live_bets)
        time.sleep(5)

        socketio.emit('aviator_status', {'state': 'flying'})

        # Matemática do Aviator (RTP)
        crash_point = round(random.uniform(1.01, 10.0), 2)

        # Simula o voo em tempo real para os clientes
        current_mult = 1.00
        while current_mult < crash_point:
            time.sleep(0.1)
            current_mult += 0.01 * (current_mult * 0.5)  # Curva exponencial suave
            if current_mult >= crash_point:
                current_mult = crash_point
            socketio.emit('aviator_tick', {'multiplier': round(current_mult, 2)})

        socketio.emit('aviator_crash', {'crash_at': crash_point})

        with app.app_context():
            db.session.add(GameHistory(game='aviator', result=str(crash_point)))
            db.session.commit()

        time.sleep(3)


# Inicia as threads dos jogos quando o servidor liga
threading.Thread(target=double_game_loop, daemon=True).start()
threading.Thread(target=aviator_game_loop, daemon=True).start()


# --- CONEXÃO WEBSOCKET DO CLIENTE ---
@socketio.on('connect')
def handle_connect():
    print("Novo player ninja conectado!")


@socketio.on('place_bet')
def handle_bet(data):
    # Aqui o frontend envia o token JWT e os dados da aposta via Socket
    # O servidor valida o saldo, deduz e adiciona o player REAL na lista 'live_bets_update'
    pass


# --- ROTAS DE CONFIGURAÇÃO DO USUÁRIO ---
@app.route('/api/user/settings', methods=['PUT'])
@token_required
def update_settings(current_user):
    data = request.json
    if 'email' in data: current_user.email = data['email']
    if 'phone' in data: current_user.phone = data['phone']
    if 'password' in data: current_user.password_hash = generate_password_hash(data['password'])
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Configurações atualizadas!'})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # No Render, usar Gunicorn com eventlet worker. Aqui, rodamos com socketio.
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
