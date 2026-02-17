import os
import time
import random
import string
import datetime
import mercadopago
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURAÇÃO CORE ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Conexão com PostgreSQL (Render) ou SQLite local
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "chave_secreta_ninja_god_mode")

db = SQLAlchemy(app)

# --- CONFIGURAÇÃO MERCADO PAGO ---
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


# --- MODELOS DE BANCO DE DADOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_status = db.Column(db.String(20), default='iniciante')  # iniciante, frequente, veterano, pro, streamer, adm
    role = db.Column(db.String(20), default='user')


class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount = db.Column(db.Float, nullable=False)
    pix_key = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default='pendente')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class ActiveGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    game_type = db.Column(db.String(20))
    bet_amount = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)


# --- INICIALIZAÇÃO DO BANCO ---
with app.app_context():
    db.create_all()

    # Cria o Admin God Mode se não existir
    if not User.query.filter_by(role='admin').first():
        hashed_pw = generate_password_hash("admin")
        admin = User(username="admin", email="admin@nexus.com", password_hash=hashed_pw, role='admin', vip_status='adm')
        db.session.add(admin)

    # Configurações padrão (RTP, Multiplicadores)
    default_configs = {
        'mines_house_edge': '50',
        'aviator_tier1': '45',  # 1.00x a 1.50x
        'aviator_tier2': '35',  # 1.50x a 2.00x
        'aviator_tier3': '15',  # 2.00x a 5.00x
        'aviator_tier4': '5',  # Acima de 5.00x
        'double_mult_red': '2.0',
        'double_mult_black': '2.0',
        'double_mult_white': '7.0'  # Branco agora é 7x!
    }
    for k, v in default_configs.items():
        if not Config.query.filter_by(key=k).first():
            db.session.add(Config(key=k, value=v))
    db.session.commit()


# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    login_input = data.get('login', '').strip()

    user = User.query.filter(
        (User.email == login_input) | (User.username == login_input) | (User.cpf == login_input) | (
                    User.phone == login_input)).first()

    if user and check_password_hash(user.password_hash, data.get('password')):
        # Token simplificado para o exemplo (em prod use JWT)
        token = f"token_{user.id}_{int(time.time())}"
        return jsonify({
            'success': True,
            'token': token,
            'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role,
                     'vip': user.vip_status}
        })
    return jsonify({'success': False, 'msg': 'Credenciais incorretas'}), 401


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter((User.email == data.get('email')) | (User.username == data.get('username')) | (
            User.cpf == data.get('cpf'))).first():
        return jsonify({'success': False, 'msg': 'Usuário, Email ou CPF já cadastrado!'})

    new_user = User(
        username=data.get('username'),
        email=data.get('email'),
        cpf=data.get('cpf'),
        phone=data.get('phone'),
        password_hash=generate_password_hash(data.get('password'))
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada!'})


# --- ROTAS DE PAGAMENTO (MERCADO PAGO) ---
@app.route('/api/deposit/pix', methods=['POST'])
def create_pix():
    data = request.json
    user = User.query.get(data.get('user_id'))
    amount = float(data.get('amount', 0))

    if not user or amount < 20:
        return jsonify({"success": False, "msg": "Valor mínimo R$ 20"})

    try:
        payment_data = {
            "transaction_amount": amount,
            "description": f"Nexus Deposit - {user.username}",
            "payment_method_id": "pix",
            "payer": {"email": user.email, "first_name": user.username}
        }
        payment = sdk.payment().create(payment_data)["response"]

        if payment.get("status") == 400:
            return jsonify({"success": False, "msg": "Erro no Mercado Pago"})

        # O webhook do MP deve ser configurado para atualizar o saldo depois.
        return jsonify({
            "success": True,
            "qr_code": payment["point_of_interaction"]["transaction_data"]["qr_code"],
            "qr_base64": payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        })
    except Exception as e:
        return jsonify({"success": False, "msg": "Erro interno MP"})


# --- PAINEL GOD MODE (ADMIN) ---
@app.route('/api/admin/dashboard', methods=['GET'])
def admin_dashboard():
    # Em produção, checar token de admin aqui!
    users = User.query.filter_by(role='user').all()
    withdrawals = Withdrawal.query.filter_by(status='pendente').all()

    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'username': u.username, 'balance': u.balance, 'vip': u.vip_status} for u in users],
        'withdrawals': [{'id': w.id, 'user': User.query.get(w.user_id).username, 'amount': w.amount, 'pix': w.pix_key}
                        for w in withdrawals],
        'plans': []  # Adicione a lógica de plans aqui se desejar buscar do banco
    })


@app.route('/api/admin/withdrawal_action', methods=['POST'])
def admin_wd_action():
    data = request.json
    wd = Withdrawal.query.get(data['id'])
    if not wd or wd.status != 'pendente': return jsonify({"success": False})

    if data['action'] == 'approve':
        wd.status = 'aprovado'
    elif data['action'] == 'reject':
        wd.status = 'rejeitado'
        # Devolve o dinheiro pro jogador!
        user = User.query.get(wd.user_id)
        user.balance += wd.amount

    db.session.commit()
    return jsonify({"success": True})


# ==========================================
# LÓGICA DE JOGOS (CASSINO SEMPRE GANHA)
# ==========================================

def get_config(key):
    return float(Config.query.filter_by(key=key).first().value)


# --- 1. DOUBLE (A LÓGICA MORTAL) ---
@app.route('/api/game/double/play', methods=['POST'])
def double_play():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet_amount = float(data['bet_amount'])
    bet_color = data['color']  # 'red', 'black', 'white'

    if user.balance < bet_amount or bet_amount <= 0:
        return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet_amount

    mults = {
        'red': get_config('double_mult_red'),  # 2.0x
        'black': get_config('double_mult_black'),  # 2.0x
        'white': get_config('double_mult_white')  # 7.0x
    }

    # Calcula quanto a casa teria que pagar em cada cenário baseando-se APENAS nas apostas reais.
    # Neste caso, como a requisição é individual, a única aposta real é a deste usuário.
    payouts = {'red': 0, 'black': 0, 'white': 0}
    payouts[bet_color] += (bet_amount * mults[bet_color])

    # A casa escolhe a cor que gera o MENOR PREJUÍZO (Min Payout)
    min_payout = min(payouts.values())

    # Cores que geram o menor prejuízo (normalmente as que o jogador NÃO escolheu)
    best_colors_for_house = [color for color, payout in payouts.items() if payout == min_payout]

    # Decide o resultado
    winning_color = random.choice(best_colors_for_house)

    # Verifica se o jogador ganhou (Nesta lógica 100% rigged, ele nunca vai ganhar,
    # a não ser que adicionemos uma pequena chance justa para disfarçar.
    # Vou adicionar 10% de chance do jogo ser justo pra ele não desconfiar rápido demais)
    if random.random() < 0.10:
        # Rola normal (10% de chance de ser honesto)
        r = random.random()
        winning_color = 'white' if r < 0.05 else ('red' if r < 0.525 else 'black')

    # Calcula prêmio
    win_amount = 0
    if winning_color == bet_color:
        win_amount = bet_amount * mults[winning_color]
        user.balance += win_amount

    db.session.commit()

    return jsonify({
        "success": True,
        "result_color": winning_color,
        "win_amount": win_amount,
        "new_balance": user.balance
    })


# --- 2. AVIATOR (LÓGICA POR FAIXAS %) ---
@app.route('/api/game/aviator/play', methods=['POST'])
def aviator_play():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet = float(data['bet_amount'])

    if user.balance < bet: return jsonify({"success": False})
    user.balance -= bet

    # Registra no BD
    ActiveGame.query.filter_by(user_id=user.id, game_type='aviator').delete()
    db.session.add(ActiveGame(user_id=user.id, game_type='aviator', bet_amount=bet))

    # Puxa % do God Mode
    t1 = get_config('aviator_tier1')  # 1.00 - 1.50
    t2 = get_config('aviator_tier2')  # 1.50 - 2.00
    t3 = get_config('aviator_tier3')  # 2.00 - 5.00
    t4 = get_config('aviator_tier4')  # > 5.00

    total = t1 + t2 + t3 + t4
    roll = random.uniform(0, total)

    crash = 1.00
    if roll <= t1:
        crash = random.uniform(1.00, 1.49)
    elif roll <= t1 + t2:
        crash = random.uniform(1.50, 1.99)
    elif roll <= t1 + t2 + t3:
        crash = random.uniform(2.00, 5.00)
    else:
        crash = random.uniform(5.01, 20.00)  # Voa alto!

    db.session.commit()
    return jsonify({"success": True, "crash_point": round(crash, 2), "new_balance": user.balance})


# --- 3. MINES (ANTI-FRAUDE E RTP) ---
@app.route('/api/game/mines/play', methods=['POST'])
def mines_play():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet = float(data['bet_amount'])

    if user.balance < bet: return jsonify({"success": False})
    user.balance -= bet

    ActiveGame.query.filter_by(user_id=user.id, game_type='mines').delete()
    db.session.add(ActiveGame(user_id=user.id, game_type='mines', bet_amount=bet))

    # Se o Edge for alto, manda a flag 'rigged' para o front forçar a bomba
    edge = get_config('mines_house_edge')
    rigged = random.uniform(0, 100) < edge

    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance, "rigged": rigged})


@app.route('/api/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    win = float(data['win_amount'])

    game = ActiveGame.query.filter_by(user_id=user.id, game_type='mines', is_active=True).first()
    if not game: return jsonify({"success": False})

    user.balance += win
    game.is_active = False
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
