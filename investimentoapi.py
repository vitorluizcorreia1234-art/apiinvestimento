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

# Configuração do Banco de Dados
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CHAVE SECRETA PARA CRIPTOGRAFIA DE TOKENS
SECRET_KEY = os.environ.get("SECRET_KEY", "chave_super_secreta_ninja_nexus_2024")
app.config['SECRET_KEY'] = SECRET_KEY

db = SQLAlchemy(app)

# --- CONFIGURAÇÃO MERCADO PAGO ---
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "TEST-00000000000000-000000-0000000000000000000000000000000-0000000")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# --- CONFIGURAÇÃO DE E-MAIL (GMAIL) ---
SMTP_USER = os.environ.get("SMTP_USER", "seu_email@gmail.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "sua_senha_de_app")


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
    # Campos para recuperação de senha
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_exp = db.Column(db.DateTime, nullable=True)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    pix_key = db.Column(db.String(100), nullable=True)
    external_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


# --- DECORADORES DE SEGURANÇA ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith("Bearer "):
            return jsonify({'success': False, 'msg': 'Token ausente ou inválido'}), 401

        token = token.split(" ")[1]
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user:
                raise Exception("Usuário não encontrado")
        except Exception as e:
            return jsonify({'success': False, 'msg': 'Sessão expirada ou inválida'}), 401

        return f(current_user, *args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role != 'admin' and current_user.vip != 'adm':
            return jsonify({'success': False, 'msg': 'Acesso negado. Apenas Administradores.'}), 403
        return f(current_user, *args, **kwargs)

    return decorated


# --- ROTAS DE AUTENTICAÇÃO E RECUPERAÇÃO ---
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(username=data['username']).first() or User.query.filter_by(email=data['email']).first():
        return jsonify({'success': False, 'msg': 'Usuário ou Email já existem.'})

    role = 'admin' if data['username'].lower() == 'admin' else 'user'
    vip = 'adm' if data['username'].lower() == 'admin' else 'iniciante'
    hashed_password = generate_password_hash(data['password'], method='pbkdf2:sha256')

    new_user = User(
        username=data['username'], email=data['email'], cpf=data['cpf'],
        phone=data['phone'], password_hash=hashed_password, role=role, vip=vip
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta Ninja criada com sucesso!'})


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['login']).first()
    if not user:
        user = User.query.filter_by(email=data['login']).first()

    if user and check_password_hash(user.password_hash, data['password']):
        token = jwt.encode({'user_id': user.id, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
                           app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({
            'success': True, 'token': token,
            'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role,
                     'vip': user.vip}
        })
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})


# --- SISTEMA DE ESQUECI A SENHA ---
def send_email_async(to_email, code):
    if SMTP_USER == "seu_email@gmail.com":
        print("Aviso: Email não configurado nas variáveis do Render. Código:", code)
        return
    try:
        msg = EmailMessage()
        msg.set_content(
            f"Olá!\n\nSeu código de recuperação de senha na NEXUS é: {code}\n\nEle expira em 15 minutos. Não compartilhe com ninguém.")
        msg['Subject'] = 'Recuperação de Senha - NEXUS'
        msg['From'] = f"Suporte NEXUS <{SMTP_USER}>"
        msg['To'] = to_email

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"Erro ao enviar email: {e}")


@app.route('/api/auth/forgot_password', methods=['POST'])
def forgot_password():
    data = request.json
    email = data.get('email')
    user = User.query.filter_by(email=email).first()

    # Sempre retorna true para evitar que hackers descubram quais emails existem na base
    if user:
        code = ''.join(random.choices(string.digits, k=6))
        user.reset_code = code
        user.reset_code_exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
        db.session.commit()
        send_email_async(user.email, code)

    return jsonify({'success': True, 'msg': 'Se o e-mail existir, o código foi enviado.'})


@app.route('/api/auth/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    code = data.get('code')
    new_password = data.get('new_password')

    user = User.query.filter_by(reset_code=code).first()

    if not user or not user.reset_code_exp or user.reset_code_exp < datetime.datetime.utcnow():
        return jsonify({'success': False, 'msg': 'Código inválido ou expirado.'})

    user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
    user.reset_code = None
    user.reset_code_exp = None
    db.session.commit()

    return jsonify({'success': True, 'msg': 'Senha atualizada com sucesso!'})


# --- ROTAS DE PAGAMENTO ---
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    if amount < 20: return jsonify({'success': False, 'msg': 'Valor mínimo de R$ 20.00'})

    payment_data = {
        "transaction_amount": amount,
        "description": f"Deposito NEXUS - {current_user.username}",
        "payment_method_id": "pix",
        "payer": {"email": current_user.email, "first_name": current_user.username}
    }

    payment_response = sdk.payment().create(payment_data)
    payment = payment_response["response"]

    if "id" in payment:
        trans = Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"]))
        db.session.add(trans)
        db.session.commit()

        return jsonify(
            {'success': True, 'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'],
             'qr_code': payment['point_of_interaction']['transaction_data']['qr_code']})

    return jsonify({'success': False, 'msg': 'Erro no Mercado Pago.'})


@app.route('/api/webhook/mercadopago', methods=['POST'])
def webhook_mp():
    data = request.json
    if data and data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id")
        if payment_id:
            payment_info = sdk.payment().get(payment_id)["response"]
            if payment_info.get("status") == "approved":
                trans = Transaction.query.with_for_update().filter_by(external_id=str(payment_id),
                                                                      status='pending').first()
                if trans:
                    trans.status = 'approved'
                    user = User.query.get(trans.user_id)
                    user.balance += trans.amount
                    db.session.commit()
    return jsonify({'success': True}), 200


@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    if amount <= 0: return jsonify({'success': False, 'msg': 'Valor inválido'})

    user = User.query.with_for_update().get(current_user.id)
    if user.balance < amount: return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    user.balance -= amount
    trans = Transaction(user_id=user.id, type='withdraw', amount=amount, pix_key=data.get('pix_key'), status='pending')
    db.session.add(trans)
    db.session.commit()

    return jsonify({'success': True, 'msg': 'Saque solicitado com sucesso!', 'new_balance': user.balance})


# --- ROTAS ADMIN E JOGOS ---
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip} for u in User.query.all()]
    wd_data = [{"id": w.id, "user": User.query.get(w.user_id).username, "amount": w.amount, "pix": w.pix_key} for w in
               Transaction.query.filter_by(type='withdraw', status='pending').all()]
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
        User.query.get(trans.user_id).balance += trans.amount

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Processado.'})


@app.route('/api/game/bet', methods=['POST'])
@token_required
def game_bet(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0: return jsonify({'success': False, 'msg': 'Aposta inválida.'})

    user = User.query.with_for_update().get(current_user.id)
    if user.balance < amount: return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    user.balance -= amount
    db.session.commit()
    return jsonify({'success': True, 'new_balance': user.balance})


@app.route('/api/game/win', methods=['POST'])
@token_required
def game_win(current_user):
    win_amount = float(request.json.get('win_amount', 0))
    if win_amount <= 0: return jsonify({'success': False, 'msg': 'Valor inválido.'})

    user = User.query.with_for_update().get(current_user.id)
    user.balance += win_amount
    db.session.commit()
    return jsonify({'success': True, 'new_balance': user.balance})


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            hashed = generate_password_hash('admin', method='pbkdf2:sha256')
            db.session.add(
                User(username='admin', email='admin@nexus.com', cpf='00000000000', phone='000', password_hash=hashed,
                     role='admin', vip='adm'))
            db.session.commit()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
