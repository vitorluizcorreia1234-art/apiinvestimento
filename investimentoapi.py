import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import datetime
import random
import string
import requests
from decimal import Decimal
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

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

SECRET_KEY = os.environ.get("SECRET_KEY")
app.config['SECRET_KEY'] = SECRET_KEY

db = SQLAlchemy(app)

# ==========================================
# CONFIGURAÇÃO MERCADO PAGO
# ==========================================
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "SEU_TOKEN_AQUI")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


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
    vip = db.Column(db.String(50), default='nenhum')  # Começa SEM VIP
    status = db.Column(db.String(20), default='active')
    ban_reason = db.Column(db.String(255), nullable=True)
    last_ip = db.Column(db.String(50), nullable=True)

    # Sistema de Indicação
    referred_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    has_deposited = db.Column(db.Boolean, default=False)


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


class InvestmentPlan(db.Model):
    __tablename__ = 'investment_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # mensal, trimestral, etc.
    yield_total = db.Column(db.Float, nullable=False)
    min_amount = db.Column(db.Float, nullable=False)
    days = db.Column(db.Integer, nullable=False)
    desc = db.Column(db.String(255), nullable=True)


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

            # Configuração padrão de VIPs (Agora com cooldown em horas)
            if not SystemConfig.query.filter_by(key='vip_settings').first():
                default_vips = {
                    "iniciante": {"name": "Iniciante", "min_deposit": 50, "tax_percent": 0.05, "max_withdraw": 1000,
                                  "cooldown_hours": 24},
                    "prata": {"name": "Prata", "min_deposit": 1000, "tax_percent": 0.03, "max_withdraw": 5000,
                              "cooldown_hours": 12},
                    "ouro": {"name": "Ouro", "min_deposit": 10000, "tax_percent": 0.0, "max_withdraw": 999999,
                             "cooldown_hours": 0}
                }
                db.session.add(SystemConfig(key='vip_settings', value=json.dumps(default_vips)))

            # Prêmio de Indicação Padrão
            if not SystemConfig.query.filter_by(key='referral_reward').first():
                db.session.add(SystemConfig(key='referral_reward', value='50.0'))

            # Cria os planos iniciais se a tabela estiver vazia
            if InvestmentPlan.query.count() == 0:
                planos_iniciais = [
                    InvestmentPlan(name="Nexus Basic", type="mensal", yield_total=30, min_amount=100, days=30,
                                   desc="Liquidez de 30 dias."),
                    InvestmentPlan(name="Nexus Advanced", type="trimestral", yield_total=100, min_amount=500, days=90,
                                   desc="Juros compostos."),
                ]
                db.session.add_all(planos_iniciais)

            # Criação do Admin Master
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
        if not token or not token.startswith("Bearer "):
            return jsonify({'success': False, 'msg': 'Sessão inválida'}), 401
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
        if current_user.role != 'admin' and current_user.vip != 'adm':
            return jsonify({'success': False, 'msg': 'Acesso negado.'}), 403
        return f(current_user, *args, **kwargs)

    return decorated


# ==========================================
# ROTAS PÚBLICAS E CONFIGURAÇÕES
# ==========================================
@app.route('/api/config/public', methods=['GET'])
def get_public_config():
    ref_reward = SystemConfig.query.get('referral_reward')
    return jsonify({
        'success': True,
        'config': {
            'referral_reward': float(ref_reward.value) if ref_reward else 50.0
        }
    })


@app.route('/api/config/plans/public', methods=['GET'])
def get_public_plans():
    plans = InvestmentPlan.query.all()
    plan_list = [
        {"id": p.id, "name": p.name, "type": p.type, "yieldTotal": p.yield_total, "min": p.min_amount, "days": p.days,
         "desc": p.desc} for p in plans]
    return jsonify({'success': True, 'plans': plan_list})


@app.route('/api/config/vip/public', methods=['GET'])
def get_public_vip():
    return jsonify({'success': True, 'vip_config': get_vip_config()})


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
@limiter.limit("3 per minute")
def register():
    data = request.json
    if User.query.filter(
            (User.username == data['username']) | (User.email == data['email']) | (User.cpf == data['cpf'])).first():
        return jsonify({'success': False, 'msg': 'Usuário já existe.'})

    # Verifica se tem ID de indicação válido
    referred_by_id = None
    if 'ref' in data and data['ref']:
        referrer = User.query.get(data['ref'])
        if referrer: referred_by_id = referrer.id

    new_user = User(
        username=data['username'], email=data['email'], cpf=data['cpf'], phone=data['phone'],
        password_hash=generate_password_hash(data['password'], method='pbkdf2:sha256'),
        role='user', vip='nenhum', referred_by=referred_by_id
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta criada!'})


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
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
# DEPÓSITOS E SUBIDA AUTOMÁTICA DE VIP / INDICAÇÃO
# ==========================================
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    amount = float(request.json.get('amount', 0))
    # TRAVA DE 20 REAIS NO BACKEND
    if amount < 20:
        return jsonify({'success': False, 'msg': 'O mínimo para depósito é R$ 20.00'})

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

                    # LÓGICA DE BÔNUS DE INDICAÇÃO COM VALOR MÍNIMO
                    if not user.has_deposited:
                        # Busca o valor mínimo exigido no banco de dados (padrão 20.0)
                        ref_min_dep = SystemConfig.query.get('referral_min_deposit')
                        min_req = float(ref_min_dep.value) if ref_min_dep else 20.0
                        
                        if trans.amount >= min_req: # Somente libera se o deposito bater a meta
                            user.has_deposited = True
                            if user.referred_by:
                                referrer = User.query.get(user.referred_by)
                                if referrer:
                                    ref_reward = SystemConfig.query.get('referral_reward')
                                    reward_amount = float(ref_reward.value) if ref_reward else 50.0
                                    referrer.balance += reward_amount
                                    db.session.add(Transaction(user_id=referrer.id, type='deposit', amount=reward_amount,
                                                               external_id='bonus_indicacao', status='approved'))

                    # LÓGICA DE SUBIDA DE VIP AUTOMÁTICA
                    total_deposits = db.session.query(db.func.sum(Transaction.amount)).filter_by(user_id=user.id,
                                                                                                 type='deposit',
                                                                                                 status='approved').scalar() or 0
                    vip_rules = get_vip_config()
                    sorted_vips = sorted(vip_rules.items(), key=lambda x: x[1].get('min_deposit', 0), reverse=True)

                    if user.vip not in ['adm', 'streamer']:
                        for vip_key, vip_data in sorted_vips:
                            if total_deposits >= vip_data.get('min_deposit', 0):
                                user.vip = vip_key
                                break
                    db.session.commit()
        except Exception as e:
            print("Erro Webhook:", e)
    return jsonify({"success": True}), 200


# ==========================================
# SAQUES COM COOLDOWN E TRAVA DIÁRIA
# ==========================================
@app.route('/api/withdraw/request', methods=['POST'])
@token_required
@limiter.limit("2 per minute")
def withdraw_request(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    full_name = data.get('full_name', '').strip()
    cpf_informado = data.get('cpf', '').strip()
    pix_key = data.get('pix_key', '').strip()

    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    # ==========================================
    # SISTEMA ANTIFRAUDE (Nome, CPF e Chave PIX)
    # ==========================================
    if cpf_informado != current_user.cpf:
        return jsonify({'success': False, 'msg': 'Operação bloqueada: O CPF informado não confere com o CPF do seu cadastro!'})

    if pix_key != current_user.cpf:
        return jsonify({'success': False, 'msg': 'Segurança: A chave PIX de destino DEVE ser obrigatoriamente o seu CPF cadastrado!'})

    if len(full_name) < 5:
        return jsonify({'success': False, 'msg': 'Informe seu nome completo verdadeiro para prosseguir.'})
    # ==========================================

    vip_rules = get_vip_config()

    if current_user.vip in ['adm', 'streamer']:
        taxa_percentual = 0.0
        max_limit = 9999999
        cooldown_hours = 0
    else:
        user_rule = vip_rules.get(current_user.vip, None)
        if not user_rule:
            return jsonify({'success': False, 'msg': 'Seu nível não permite saques no momento. Suba de VIP.'})

        taxa_percentual = user_rule.get('tax_percent', 0.05)
        max_limit = user_rule.get('max_withdraw', 1000)
        cooldown_hours = user_rule.get('cooldown_hours', 24)

    # VERIFICAÇÃO DE COOLDOWN (Tempo entre saques)
    if cooldown_hours > 0:
        last_wd = Transaction.query.filter_by(user_id=current_user.id, type='withdraw').filter(
            Transaction.status != 'rejected').order_by(Transaction.created_at.desc()).first()
        if last_wd:
            time_since_last = (datetime.datetime.utcnow() - last_wd.created_at).total_seconds() / 3600
            if time_since_last < cooldown_hours:
                hours_left = int(cooldown_hours - time_since_last)
                return jsonify({'success': False,
                                'msg': f'Aguarde o tempo de segurança. Próximo saque liberado em aproximadamente {hours_left} horas.'})

    # VERIFICAÇÃO LIMITE DIÁRIO
    hoje = datetime.datetime.utcnow().date()
    inicio_do_dia = datetime.datetime.combine(hoje, datetime.time.min)
    saques_hoje = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id, Transaction.type == 'withdraw',
        Transaction.status != 'rejected', Transaction.created_at >= inicio_do_dia
    ).scalar() or 0

    if (saques_hoje + amount) > max_limit:
        limite_restante = max(0, max_limit - saques_hoje)
        return jsonify({'success': False,
                        'msg': f'Limite diário excedido! Restante hoje: R$ {limite_restante:.2f}'})

    # FINALIZAÇÃO
    valor_liquido = amount - (amount * taxa_percentual)
    current_user.balance -= amount

    # Vamos guardar o Nome Completo na coluna external_id para mostrar no Admin
    db.session.add(
        Transaction(user_id=current_user.id, type='withdraw', amount=valor_liquido, pix_key=pix_key,
                    external_id=full_name, status='pending'))
    db.session.commit()

    return jsonify({'success': True, 'msg': f'Saque solicitado! Líquido: R$ {valor_liquido:.2f}',
                    'new_balance': current_user.balance})

# ==========================================
# PAINEL ADMIN: DASHBOARD E GESTÃO
# ==========================================
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users_data = []
    for u in User.query.all():
        # Calcula quantos referidos esse usuário tem
        ref_count = User.query.filter_by(referred_by=u.id).count()
        users_data.append({
            "id": u.id, "username": u.username, "email": u.email, "cpf": u.cpf,
            "balance": u.balance, "vip": u.vip, "status": u.status, "referrals": ref_count
        })

    wd_data = []
    for w in Transaction.query.filter_by(type='withdraw', status='pending').all():
        u_obj = User.query.get(w.user_id)
        wd_data.append({
            "id": w.id, 
            "user": u_obj.username if u_obj else 'Deletado', 
            "cpf": u_obj.cpf if u_obj else 'N/A',
            "full_name": w.external_id or 'Não Informado',
            "amount": w.amount, 
            "pix": w.pix_key
        })

    # Puxa configuração geral para o dash
    ref_reward = SystemConfig.query.get('referral_reward')
    ref_min = SystemConfig.query.get('referral_min_deposit')

    return jsonify({
        'success': True,
        'users': users_data,
        'withdrawals': wd_data,
        'config': {
            'referral_reward': float(ref_reward.value) if ref_reward else 50.0,
            'referral_min_deposit': float(ref_min.value) if ref_min else 20.0
        }
    })


# ==========================================
# PAINEL ADMIN: CONFIGURAÇÕES GERAIS E PLANOS
# ==========================================
@app.route('/api/admin/config/general', methods=['POST'])
@admin_required
def admin_config_general(current_user):
    data = request.json
    if 'referral_reward' in data:
        cfg = SystemConfig.query.get('referral_reward')
        if not cfg:
            cfg = SystemConfig(key='referral_reward')
            db.session.add(cfg)
        cfg.value = str(data['referral_reward'])
        
    if 'referral_min_deposit' in data:
        cfg_min = SystemConfig.query.get('referral_min_deposit')
        if not cfg_min:
            cfg_min = SystemConfig(key='referral_min_deposit')
            db.session.add(cfg_min)
        cfg_min.value = str(data['referral_min_deposit'])
        
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Configurações salvas!'})


@app.route('/api/admin/config/plans', methods=['POST'])
@admin_required
def manage_plans_config(current_user):
    data_plans = request.json
    try:
        InvestmentPlan.query.delete()  # Limpa os antigos
        for p in data_plans:
            db.session.add(InvestmentPlan(
                name=p['name'], type=p['type'], yield_total=p['yieldTotal'],
                min_amount=p['min'], days=p['days'], desc=p.get('desc', '')
            ))
        db.session.commit()
        return jsonify({'success': True, 'msg': 'Planos atualizados no aplicativo principal!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)})


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


# ==========================================
# PAINEL ADMIN: AÇÕES DE USUÁRIO
# ==========================================
@app.route('/api/admin/withdraw/action', methods=['POST'])
@admin_required
def admin_withdraw_action(current_user):
    data = request.json
    trans = Transaction.query.get(data.get('id'))
    if trans and trans.status == 'pending':
        # Converte a ação do admin para o termo que o painel do usuário entende
        acao_admin = data.get('action')
        trans.status = 'approved' if acao_admin == 'approve' else 'rejected'
        
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
        Investment.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
    return jsonify({'success': True})


# ==========================================
# INVESTIMENTOS (COMPRA E LISTAGEM)
# ==========================================
@app.route('/api/investment/buy', methods=['POST'])
@token_required
def buy_investment(current_user):
    data = request.json
    plan_id = data.get('plan_id')
    amount = float(data.get('amount', 0))

    # BUSCAR DADOS REAIS DO PLANO NO BANCO (Proteção contra hackers)
    plan = InvestmentPlan.query.get(plan_id)
    if not plan:
        return jsonify({'success': False, 'msg': 'Plano inválido ou não encontrado.'})

    # Verifica se o valor enviado respeita o mínimo do plano oficial
    if amount < plan.min_amount:
        return jsonify({'success': False, 'msg': f'O valor mínimo para este plano é R$ {plan.min_amount:.2f}'})

    # Verifica se tem saldo
    if current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    # Desconta o saldo
    current_user.balance -= amount
    
    # Salva usando os dados do banco (plan.yield_total e plan.days) ignorando o que o frontend enviou
    novo_investimento = Investment(
        user_id=current_user.id, 
        plan_id=plan.id, 
        name=plan.name, 
        amount=amount,
        yield_total=plan.yield_total, 
        days=plan.days
    )
    db.session.add(novo_investimento)
    db.session.commit()
    
    return jsonify({'success': True, 'msg': 'Plano ativado com sucesso!', 'new_balance': current_user.balance})


@app.route('/api/user/transactions', methods=['GET'])
@token_required
def get_user_transactions(current_user):
    txs = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.created_at.desc()).all()
    return jsonify({'success': True, 'transactions': [
        {'id': t.id, 'type': t.type, 'amount': t.amount, 'status': t.status,
         'date': t.created_at.strftime("%d/%m/%Y %H:%M")}
        for t in txs
    ]})


@app.route('/api/investment/active', methods=['GET'])
@token_required
def get_active_investments(current_user):
    investments = Investment.query.filter_by(user_id=current_user.id, claimed=False).all()
    return jsonify({'success': True, 'investments': [
        {'id': i.id, 'name': i.name, 'amount': i.amount, 'yieldTotal': i.yield_total, 'days': i.days,
         'startTime': int(i.start_time.timestamp() * 1000)} for i in investments]})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)
