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
import json
import mercadopago

# ==========================================
# CONFIGURAÇÃO CORE E SEGURANÇA
# ==========================================
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    role = db.Column(db.String(20), default='user')
    vip = db.Column(db.String(50), default='iniciante')  # Dinâmico agora
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_exp = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='active')
    ban_reason = db.Column(db.String(255), nullable=True)
    last_ip = db.Column(db.String(50), nullable=True)


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    pix_key = db.Column(db.String(100), nullable=True)
    external_id = db.Column(db.String(100), nullable=True)
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
# INICIALIZAÇÃO E CONFIGURAÇÃO DINÂMICA
# ==========================================
def setup_database():
    with app.app_context():
        try:
            db.create_all()

            # Cria a configuração padrão de VIPs se não existir
            if not SystemConfig.query.filter_by(key='vip_settings').first():
                default_vips = {
                    "iniciante": {"name": "Iniciante", "min_deposit": 0, "tax_percent": 0.05, "max_withdraw": 1000},
                    "prata": {"name": "Prata", "min_deposit": 1000, "tax_percent": 0.03, "max_withdraw": 5000},
                    "ouro": {"name": "Ouro", "min_deposit": 10000, "tax_percent": 0.0, "max_withdraw": 999999}
                }
                db.session.add(SystemConfig(key='vip_settings', value=json.dumps(default_vips)))

            if not User.query.filter_by(username='admin').first():
                hashed = generate_password_hash('Ravizinho@4000', method='pbkdf2:sha256')
                admin = User(username='admin', email='admin@nexus.com', cpf='00000000000', phone='000',
                             password_hash=hashed, role='admin', vip='adm')
                db.session.add(admin)

            db.session.commit()
            print(">>> BANCO DE DADOS SINCRONIZADO <<<")
        except Exception as e:
            print(f">>> ERRO DB: {e} <<<")


setup_database()


def get_vip_config():
    config = SystemConfig.query.get('vip_settings')
    return json.loads(config.value) if config else {}


# ==========================================
# MIDDLEWARES
# ==========================================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith("Bearer "): return jsonify(
            {'success': False, 'msg': 'Sessão inválida'}), 401
        try:
            data = jwt.decode(token.split(" ")[1], app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user or current_user.status == 'banned': raise Exception()
            current_user.last_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            db.session.commit()
        except:
            return jsonify({'success': False, 'msg': 'Sessão expirada ou banida.'}), 401
        return f(current_user, *args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role != 'admin' and current_user.vip != 'adm': return jsonify(
            {'success': False, 'msg': 'Acesso negado.'}), 403
        return f(current_user, *args, **kwargs)

    return decorated


# ==========================================
# ROTAS AUTH & USER
# ==========================================
@app.route('/api/user/me', methods=['GET'])
@token_required
def get_me(current_user):
    return jsonify({'success': True,
                    'user': {'id': current_user.id, 'username': current_user.username, 'balance': current_user.balance,
                             'role': current_user.role, 'vip': current_user.vip, 'email': current_user.email}})


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter(
            (User.username == data['username']) | (User.email == data['email']) | (User.cpf == data['cpf'])).first():
        return jsonify({'success': False, 'msg': 'Usuário já existe.'})
    new_user = User(username=data['username'], email=data['email'], cpf=data['cpf'], phone=data['phone'],
                    password_hash=generate_password_hash(data['password'], method='pbkdf2:sha256'), role='user',
                    vip='iniciante')
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada!'})


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter(
        (User.username == data['login']) | (User.email == data['login']) | (User.cpf == data['login'])).first()
    if user and check_password_hash(user.password_hash, data['password']):
        if user.status == 'banned': return jsonify({'success': False, 'msg': f'Banido: {user.ban_reason}'}), 403
        token = jwt.encode({'user_id': user.id, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
                           app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'success': True, 'token': token,
                        'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role,
                                 'vip': user.vip}})
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})


# ==========================================
# DEPÓSITOS E SUBIDA AUTOMÁTICA DE VIP
# ==========================================
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    amount = float(request.json.get('amount', 0))
    if amount < 20: return jsonify({'success': False, 'msg': 'Mínimo de depósito é R$ 20.00'})
    try:
        payment = sdk.payment().create(
            {"transaction_amount": amount, "description": f"Deposito - {current_user.username}",
             "payment_method_id": "pix", "payer": {"email": current_user.email}})["response"]
        if "id" in payment:
            db.session.add(
                Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"]),
                            status='pending'))
            db.session.commit()
            return jsonify({'success': True,
                            'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'],
                            'qr_code': payment['point_of_interaction']['transaction_data']['qr_code']})
    except:
        pass
    return jsonify({'success': False, 'msg': 'Erro ao comunicar com o Banco.'})


@app.route('/api/webhook/mercadopago', methods=['POST'])
def mp_webhook():
    if request.args.get("type") == "payment":
        try:
            payment_info = sdk.payment().get(request.args.get("data.id"))["response"]
            if payment_info.get("status") == "approved":
                trans = Transaction.query.filter_by(external_id=str(request.args.get("data.id")),
                                                    status='pending').first()
                if trans:
                    trans.status = 'approved'
                    user = User.query.get(trans.user_id)
                    user.balance += trans.amount
                    db.session.commit()

                    # LÓGICA DE SUBIDA DE VIP AUTOMÁTICA
                    total_deposits = db.session.query(db.func.sum(Transaction.amount)).filter_by(user_id=user.id,
                                                                                                 type='deposit',
                                                                                                 status='approved').scalar() or 0
                    vip_rules = get_vip_config()

                    # Ordena os VIPs do maior requisito para o menor
                    sorted_vips = sorted(vip_rules.items(), key=lambda x: x[1].get('min_deposit', 0), reverse=True)

                    if user.vip not in ['adm', 'streamer']:  # Não rebaixa admins/influencers
                        for vip_key, vip_data in sorted_vips:
                            if total_deposits >= vip_data.get('min_deposit', 0):
                                user.vip = vip_key
                                break
                    db.session.commit()
        except Exception as e:
            print("Erro Webhook:", e)
    return jsonify({"success": True}), 200


# ==========================================
# SAQUES COM LIMITES DO PAINEL ADMIN (TRAVA DIÁRIA)
# ==========================================
@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    # Puxa as regras ao vivo do banco
    vip_rules = get_vip_config()

    # Se o usuário for ADM ou Streamer, ele não tem limites. Se não, puxa a regra dele ou cai pro "iniciante"
    if current_user.vip in ['adm', 'streamer']:
        taxa_percentual = 0.0
        max_limit = 9999999
    else:
        user_rule = vip_rules.get(current_user.vip, vip_rules.get('iniciante', {}))
        taxa_percentual = user_rule.get('tax_percent', 0.05)
        max_limit = user_rule.get('max_withdraw', 1000)

    # --- NOVA LÓGICA DE LIMITE DIÁRIO BLINDADO ---
    # Pega o começo do dia de hoje (00:00)
    hoje = datetime.datetime.utcnow().date()
    inicio_do_dia = datetime.datetime.combine(hoje, datetime.time.min)

    # Soma todos os saques (pendentes ou aprovados) que o usuário já pediu hoje
    saques_hoje = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id,
        Transaction.type == 'withdraw',
        Transaction.status != 'rejected',  # Ignora se o admin recusou o saque
        Transaction.created_at >= inicio_do_dia
    ).scalar() or 0

    # Bloqueia se a tentativa atual + o que ele já sacou hoje passar do limite
    if (saques_hoje + amount) > max_limit:
        limite_restante = max_limit - saques_hoje
        limite_restante = max(0, limite_restante)  # Evita número negativo
        return jsonify({
            'success': False,
            'msg': f'Limite diário excedido! O máximo por dia para o nível {current_user.vip.upper()} é R$ {max_limit}. Você ainda pode sacar R$ {limite_restante:.2f} hoje.'
        })
    # ---------------------------------------------

    # Se passou da trava diária, calcula a taxa e finaliza o pedido
    valor_liquido = amount - (amount * taxa_percentual)
    current_user.balance -= amount

    db.session.add(
        Transaction(user_id=current_user.id, type='withdraw', amount=valor_liquido, pix_key=request.json.get('pix_key'),
                    status='pending')
    )
    db.session.commit()

    return jsonify({
        'success': True,
        'msg': f'Saque solicitado! Líquido: R$ {valor_liquido:.2f}',
        'new_balance': current_user.balance
    })


# ==========================================
# INVESTIMENTOS
# ==========================================
@app.route('/api/investment/buy', methods=['POST'])
@token_required
def buy_investment(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    if amount <= 0 or current_user.balance < amount: return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})
    current_user.balance -= amount
    db.session.add(
        Investment(user_id=current_user.id, plan_id=data.get('plan_id'), name=data.get('name'), amount=amount,
                   yield_total=float(data.get('yieldTotal', 0)), days=int(data.get('days', 0))))
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Plano ativado!', 'new_balance': current_user.balance})


@app.route('/api/investment/active', methods=['GET'])
@token_required
def get_active_investments(current_user):
    investments = Investment.query.filter_by(user_id=current_user.id, claimed=False).all()
    return jsonify({'success': True, 'investments': [
        {'id': i.id, 'name': i.name, 'amount': i.amount, 'yieldTotal': i.yield_total, 'days': i.days,
         'startTime': int(i.start_time.timestamp() * 1000)} for i in investments]})


# ==========================================
# PAINEL ADMIN E GERENCIAMENTO DE VIP
# ==========================================
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = [{"id": u.id, "username": u.username, "email": u.email, "cpf": u.cpf, "balance": u.balance, "vip": u.vip,
              "status": u.status} for u in User.query.all()]
    wd_data = []
    for w in Transaction.query.filter_by(type='withdraw', status='pending').all():
        u_obj = User.query.get(w.user_id)
        wd_data.append(
            {"id": w.id, "user": u_obj.username if u_obj else 'Deletado', "amount": w.amount, "pix": w.pix_key})
    return jsonify({'success': True, 'users': users, 'withdrawals': wd_data})


@app.route('/api/admin/withdraw/action', methods=['POST'])
@admin_required
def admin_withdraw_action(current_user):
    data = request.json
    trans = Transaction.query.get(data.get('id'))
    if trans and trans.status == 'pending':
        trans.status = data.get('action')
        if trans.status == 'rejected':
            user = User.query.get(trans.user_id)
            if user: user.balance += trans.amount
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'msg': 'Erro ao processar.'})


@app.route('/api/admin/user/update', methods=['POST'])
@admin_required
def admin_user_update(current_user):
    data = request.json
    user = User.query.get(data.get('id'))
    if user:
        if 'balance_add' in data: user.balance += float(data['balance_add'])
        if 'vip' in data:
            user.vip = data['vip']
            user.role = 'admin' if data['vip'] == 'adm' else 'user'
        if 'status' in data: user.status = data['status']
        if 'ban_reason' in data: user.ban_reason = data['ban_reason']
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False})


@app.route('/api/admin/user/delete', methods=['POST'])
@admin_required
def admin_user_delete(current_user):
    user = User.query.get(request.json.get('id'))
    if user:
        Transaction.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
    return jsonify({'success': True})


# --- ROTA NOVA: LER E EDITAR AS REGRAS VIP DO PAINEL ---
@app.route('/api/admin/config/vip', methods=['GET', 'POST'])
@admin_required
def manage_vip_config(current_user):
    config = SystemConfig.query.get('vip_settings')
    if request.method == 'GET':
        return jsonify({'success': True, 'vip_config': json.loads(config.value) if config else {}})

    if request.method == 'POST':
        if config:
            config.value = json.dumps(request.json)
        else:
            db.session.add(SystemConfig(key='vip_settings', value=json.dumps(request.json)))
        db.session.commit()
        return jsonify({'success': True, 'msg': 'Configurações VIP atualizadas!'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)
