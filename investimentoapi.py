import os
import datetime
import random
import string
import smtplib
import requests
import threading
from decimal import Decimal
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import json
import mercadopago

# ==========================================
# CONFIGURAÇÃO CORE E SEGURANÇA
# ==========================================
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração do Banco de Dados (Ajuste robusto para Render/PostgreSQL)
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
# Correção crítica para o Render (SQLAlchemy requer postgresql://)
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}  # Evita queda de conexão
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CHAVE SECRETA PARA CRIPTOGRAFIA DE TOKENS (Variável de ambiente no Render)
SECRET_KEY = os.environ.get("SECRET_KEY", "chave_super_secreta_ninja_nexus_2026")
app.config['SECRET_KEY'] = SECRET_KEY

db = SQLAlchemy(app)

# ==========================================
# CONFIGURAÇÃO MERCADO PAGO E E-MAIL
# ==========================================
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN",
                                 "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

SMTP_USER = os.environ.get("SMTP_USER", "nexusinvestimento24@gmail.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "pzvn fuuo cavm ljay")

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")


def enviar_email_recuperacao(destino, codigo):
    print(f">>> [THREAD] Iniciando envio via API para {destino}...", flush=True)
    if not BREVO_API_KEY:
        print(">>> [THREAD] Chave do Brevo não configurada. Simulando envio.", flush=True)
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    payload = {
        "sender": {"name": "Suporte NEXUS", "email": "nexusinvestimento24@gmail.com"},
        "to": [{"email": destino}],
        "subject": "Código de Recuperação - NEXUS",
        "htmlContent": f"<h2>Olá!</h2><p>O seu código de recuperação de senha da plataforma NEXUS é: <strong><span style='font-size: 24px; color: #7000ff;'>{codigo}</span></strong></p><p>Este código expira em 15 minutos.</p>"
    }
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201, 202]:
            print(f">>> [THREAD] E-mail enviado com sucesso via API para {destino}!", flush=True)
            return True
        else:
            print(f">>> [THREAD] Erro na API: {response.text}", flush=True)
            return False
    except Exception as e:
        print(">>> [THREAD] Erro crítico ao chamar API de e-mail:", e, flush=True)
        return False


# ==========================================
# MODELOS DE BANCO DE DADOS
# ==========================================
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    role = db.Column(db.String(20), default='user')  # 'user' ou 'admin'
    vip = db.Column(db.String(20), default='iniciante')  # 'iniciante', 'bronze', 'prata', 'ouro', 'streamer', 'adm'
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_exp = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='active')  # 'active' ou 'banned'
    ban_reason = db.Column(db.String(255), nullable=True)
    last_ip = db.Column(db.String(50), nullable=True)


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'deposit' ou 'withdraw'
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'approved', 'rejected'
    pix_key = db.Column(db.String(100), nullable=True)
    external_id = db.Column(db.String(100), nullable=True)  # ID do MercadoPago
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class Investment(db.Model):
    __tablename__ = 'investments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    yield_total = db.Column(db.Float, nullable=False)
    days = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    claimed = db.Column(db.Boolean, default=False)


class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=False)


# ==========================================
# INICIALIZAÇÃO SEGURA DO BANCO DE DADOS
# ==========================================
def setup_database():
    with app.app_context():
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                # SENHA SOLICITADA PARA O ADMIN
                hashed = generate_password_hash('Ravizinho@4000', method='pbkdf2:sha256')
                admin = User(
                    username='admin',
                    email='admin@nexus.com',
                    cpf='00000000000',
                    phone='000',
                    password_hash=hashed,
                    role='admin',
                    vip='adm'
                )
                db.session.add(admin)
                db.session.commit()
                print(">>> BANCO DE DADOS SINCRONIZADO E ADMIN CRIADO: admin / Ravizinho@4000 <<<")
            else:
                print(">>> BANCO DE DADOS PRONTO (ADMIN JÁ EXISTE) <<<")
        except Exception as e:
            print(f">>> ERRO AO SINCRONIZAR BANCO DE DADOS: {e} <<<")


setup_database()


# ==========================================
# DECORADORES DE SEGURANÇA (MIDDLEWARES)
# ==========================================
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

            # --- SISTEMA DE BLOQUEIO ABSOLUTO ---
            if current_user.status == 'banned':
                return jsonify({'success': False, 'msg': f'CONTA CONGELADA. Motivo: {current_user.ban_reason}'}), 403

            current_user.last_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            db.session.commit()

        except Exception:
            return jsonify({'success': False, 'msg': 'Sessão expirada. Refaça o login.'}), 401

        return f(current_user, *args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role != 'admin' and current_user.vip != 'adm':
            return jsonify({'success': False, 'msg': 'Acesso negado. GOD MODE necessário.'}), 403
        return f(current_user, *args, **kwargs)

    return decorated


# ==========================================
# ROTAS DE AUTENTICAÇÃO E PERFIL
# ==========================================
@app.route('/api/user/me', methods=['GET'])
@token_required
def get_me(current_user):
    return jsonify({
        'success': True,
        'user': {
            'id': current_user.id, 'username': current_user.username,
            'balance': current_user.balance, 'role': current_user.role,
            'vip': current_user.vip, 'email': current_user.email
        }
    })


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter(
            (User.username == data['username']) | (User.email == data['email']) | (User.cpf == data['cpf'])).first():
        return jsonify({'success': False, 'msg': 'Usuário, Email ou CPF já cadastrados.'})

    hashed_password = generate_password_hash(data['password'], method='pbkdf2:sha256')
    new_user = User(
        username=data['username'], email=data['email'], cpf=data['cpf'],
        phone=data['phone'], password_hash=hashed_password,
        role='user', vip='iniciante'
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada com sucesso!'})


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter(
        (User.username == data['login']) | (User.email == data['login']) | (User.cpf == data['login'])).first()

    if user and check_password_hash(user.password_hash, data['password']):
        if user.status == 'banned':
            return jsonify({'success': False, 'msg': f'Sua conta foi banida. Motivo: {user.ban_reason}'}), 403

        token = jwt.encode({'user_id': user.id, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
                           app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({
            'success': True, 'token': token,
            'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role,
                     'vip': user.vip}
        })
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})


# ==========================================
# FINANCEIRO (DEPÓSITO / SAQUE)
# ==========================================
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    amount = float(request.json.get('amount', 0))
    if amount < 20: return jsonify({'success': False, 'msg': 'Mínimo de depósito é R$ 20.00'})

    try:
        payment_data = {
            "transaction_amount": amount,
            "description": f"Deposito NEXUS - {current_user.username}",
            "payment_method_id": "pix",
            "payer": {"email": current_user.email, "first_name": current_user.username}
        }
        payment = sdk.payment().create(payment_data)["response"]

        if "id" in payment:
            db.session.add(
                Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"]),
                            status='pending'))
            db.session.commit()
            return jsonify({
                'success': True,
                'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'],
                'qr_code': payment['point_of_interaction']['transaction_data']['qr_code']
            })
    except Exception as e:
        print("Erro MercadoPago:", e)

    return jsonify({'success': False, 'msg': 'Erro ao comunicar com o Banco.'})


# WEBHOOK DO MERCADO PAGO (APROVAÇÃO AUTOMÁTICA)
@app.route('/api/webhook/mercadopago', methods=['POST'])
def mp_webhook():
    data = request.args
    if data.get("type") == "payment":
        payment_id = data.get("data.id")
        try:
            payment_info = sdk.payment().get(payment_id)["response"]
            if payment_info.get("status") == "approved":
                external_id = str(payment_id)
                trans = Transaction.query.filter_by(external_id=external_id, status='pending').first()

                if trans:
                    trans.status = 'approved'
                    user = User.query.get(trans.user_id)
                    if user:
                        user.balance += trans.amount
                    db.session.commit()
                    print(f">>> DEPOSITO APROVADO: R$ {trans.amount} para {user.username}")
        except Exception as e:
            print(">>> Erro no Webhook:", e)

    return jsonify({"success": True}), 200


@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente ou valor inválido.'})

    # --- LÓGICA DE VIP JUSTA ---
    taxa_percentual = 0.05  # Bronze/Iniciante
    if current_user.vip == 'prata':
        taxa_percentual = 0.03
    elif current_user.vip in ['ouro', 'adm', 'streamer']:
        taxa_percentual = 0.00  # Saque livre!

    taxa_retida = amount * taxa_percentual
    valor_liquido = amount - taxa_retida

    # Desconta o valor total da banca do usuário
    current_user.balance -= amount

    # Registra o valor líquido para pagamento
    db.session.add(
        Transaction(user_id=current_user.id, type='withdraw', amount=valor_liquido, pix_key=request.json.get('pix_key'),
                    status='pending'))
    db.session.commit()

    return jsonify({
        'success': True,
        'msg': f'Saque solicitado! Valor líquido: R$ {valor_liquido:.2f} (Taxa: {taxa_percentual * 100}%).',
        'new_balance': current_user.balance
    })


# ==========================================
# ROTAS DE INVESTIMENTO (NUVEM)
# ==========================================
@app.route('/api/investment/buy', methods=['POST'])
@token_required
def buy_investment(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    plan_id = data.get('plan_id')
    name = data.get('name')
    yield_total = float(data.get('yieldTotal', 0))
    days = int(data.get('days', 0))

    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    current_user.balance -= amount

    new_inv = Investment(user_id=current_user.id, plan_id=plan_id, name=name, amount=amount, yield_total=yield_total,
                         days=days)
    db.session.add(new_inv)
    db.session.commit()

    return jsonify({'success': True, 'msg': 'Plano ativado!', 'new_balance': current_user.balance})


@app.route('/api/investment/active', methods=['GET'])
@token_required
def get_active_investments(current_user):
    investments = Investment.query.filter_by(user_id=current_user.id, claimed=False).all()
    inv_list = []
    for inv in investments:
        inv_list.append({
            'id': inv.id, 'plan_id': inv.plan_id, 'name': inv.name,
            'amount': inv.amount, 'yieldTotal': inv.yield_total, 'days': inv.days,
            'startTime': int(inv.start_time.timestamp() * 1000)
        })
    return jsonify({'success': True, 'investments': inv_list})


# ==========================================
# TERMINAL DE COMANDO - PAINEL ADMIN NINJA
# ==========================================
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = [{"id": u.id, "username": u.username, "email": u.email, "cpf": u.cpf, "balance": u.balance, "vip": u.vip,
              "status": u.status}
             for u in User.query.all()]

    wd_data = []
    for w in Transaction.query.filter_by(type='withdraw', status='pending').all():
        u_obj = User.query.get(w.user_id)
        uname = u_obj.username if u_obj else 'Deletado'
        wd_data.append({"id": w.id, "user": uname, "amount": w.amount, "pix": w.pix_key})

    return jsonify({'success': True, 'users': users, 'withdrawals': wd_data})


@app.route('/api/admin/withdraw/action', methods=['POST'])
@admin_required
def admin_withdraw_action(current_user):
    data = request.json
    trans = Transaction.query.get(data.get('id'))
    if not trans or trans.status != 'pending':
        return jsonify({'success': False, 'msg': 'Erro ou saque já processado.'})

    if data.get('action') == 'approve':
        trans.status = 'approved'
    else:
        trans.status = 'rejected'
        target_user = User.query.get(trans.user_id)
        if target_user:
            target_user.balance += trans.amount  # Estorna o valor para a conta

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Ação executada com sucesso.'})


@app.route('/api/admin/user/update', methods=['POST'])
@admin_required
def admin_user_update(current_user):
    data = request.json
    target_user = User.query.get(data.get('id'))
    if not target_user:
        return jsonify({'success': False, 'msg': 'Usuário não encontrado.'})

    # AÇÕES DO PAINEL ADMIN
    if 'balance_add' in data:
        target_user.balance += float(data['balance_add'])
    if 'vip' in data:
        target_user.vip = data['vip']
        target_user.role = 'admin' if data['vip'] == 'adm' else 'user'
    if 'status' in data:
        target_user.status = data['status']
    if 'ban_reason' in data:
        target_user.ban_reason = data['ban_reason']

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Usuário atualizado com sucesso!'})


@app.route('/api/admin/user/delete', methods=['POST'])
@admin_required
def admin_user_delete(current_user):
    target_user = User.query.get(request.json.get('id'))
    if target_user:
        Transaction.query.filter_by(user_id=target_user.id).delete()
        db.session.delete(target_user)
        db.session.commit()
    return jsonify({'success': True})

# ==========================================
# INICIALIZAÇÃO DA APLICAÇÃO
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
