from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import mercadopago
import datetime
import os
import random
import time
import string
import re

app = Flask(__name__)

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    db_url = "sqlite:///nexus.db"

# Correção para o Render (Postgres)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "nexus_ultra_secure_key_v4_lion_mode"

db = SQLAlchemy(app)
CORS(app)

# --- CONFIGURAÇÃO MERCADO PAGO ---
# Seu Token de Acesso (PRODUÇÃO)
MP_ACCESS_TOKEN = "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801"
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

ADMIN_PIN = "1234"


# --- MODELOS (TABELAS) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)


class Deposit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    payment_id_mp = db.Column(db.String(50), unique=True, nullable=False)  # ID do Mercado Pago
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)


class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    total_rate = db.Column(db.Float, nullable=False)
    min_entry = db.Column(db.Float, default=30.0)


class Investment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name = db.Column(db.String(50))
    amount = db.Column(db.Float, nullable=False)
    start_date = db.Column(db.DateTime, default=datetime.datetime.now)
    end_date = db.Column(db.DateTime, nullable=False)
    final_return = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='ativo')


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(80))
    amount = db.Column(db.Float, nullable=False)
    pix_key = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pendente')
    date = db.Column(db.DateTime, default=datetime.datetime.now)


class FinancialLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50))
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=datetime.datetime.now)


class GameConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mult_black = db.Column(db.Float, default=2.0)
    mult_red = db.Column(db.Float, default=2.0)
    mult_white = db.Column(db.Float, default=14.0)
    chance_black = db.Column(db.Float, default=45.0)
    chance_red = db.Column(db.Float, default=45.0)
    chance_white = db.Column(db.Float, default=10.0)
    mines_edge = db.Column(db.Float, default=30.0)
    aviator_max_mult = db.Column(db.Float, default=10.0)
    aviator_edge = db.Column(db.Float, default=10.0)
    aviator_prob_low = db.Column(db.Float, default=60.0)  # Chance de ser < 1.50x
    aviator_prob_med = db.Column(db.Float, default=25.0)  # Chance de 1.50x até 2.00x
    aviator_prob_high = db.Column(db.Float, default=10.0)  # Chance de 2.00x até 5.00x
    force_crash_rounds = db.Column(db.Integer, default=0)


class SystemStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active_invest = db.Column(db.Boolean, default=True)
    active_double = db.Column(db.Boolean, default=True)
    active_mines = db.Column(db.Boolean, default=True)
    active_aviator = db.Column(db.Boolean, default=True)


# --- INIT ---
with app.app_context():
    db.create_all()
    if not Plan.query.first():
        db.session.add(Plan(name="Crash 24h", duration_minutes=1440, total_rate=0.05, min_entry=50))
    if not GameConfig.query.first():
        db.session.add(GameConfig())
    if not SystemStatus.query.first():
        db.session.add(SystemStatus())
    db.session.commit()


# --- AUXILIARES ---
def clean_input(text):
    """Remove caracteres especiais, mantendo apenas números"""
    if not text: return ""
    return re.sub(r'[^0-9]', '', str(text))


def check_maintenance(game_type):
    s = SystemStatus.query.first()
    if not s: return False
    if game_type == 'invest' and not s.active_invest: return True
    if game_type == 'double' and not s.active_double: return True
    if game_type == 'mines' and not s.active_mines: return True
    if game_type == 'aviator' and not s.active_aviator: return True
    return False


def registrar_log(tipo, valor, desc):
    log = FinancialLog(type=tipo, amount=valor, description=desc)
    db.session.add(log)

current_round_bets = {"red": 0, "black": 0, "white": 0, "players": []}

# --- ROTAS DE AUTH (LOGIN POWER) ---

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    login_input = data.get('login', '').strip()
    password = data.get('password', '').strip()

    # Tenta limpar o input caso seja CPF ou Telefone (apenas números)
    clean_login = clean_input(login_input)

    # Busca em TODOS os campos possíveis
    # 1. Username ou Email (busca direta)
    user = User.query.filter((User.username == login_input) | (User.email == login_input)).first()

    # 2. Se não achou e tem números, tenta CPF ou Phone
    if not user and clean_login:
        user = User.query.filter((User.cpf == clean_login) | (User.phone == clean_login)).first()

    if user and user.password == password:
        return jsonify({
            "success": True,  # Flag importante pro front
            "id": user.id,
            "username": user.username,
            "balance": user.balance,
            "vip_level": user.vip_level
        })

    return jsonify({"erro": True, "msg": "Dados de acesso incorretos."}), 401


@app.route('/register', methods=['POST'])
def register():
    data = request.json

    # Sanitização
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    cpf = clean_input(data.get('cpf'))
    phone = clean_input(data.get('phone'))
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({"erro": True, "msg": "Preencha todos os campos obrigatórios."}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"erro": True, "msg": "Usuário indisponível."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"erro": True, "msg": "Email já cadastrado."}), 400
    if cpf and User.query.filter_by(cpf=cpf).first():
        return jsonify({"erro": True, "msg": "CPF já existe no sistema."}), 400

    new_user = User(username=username, email=email, password=password, cpf=cpf, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})


# -- RECUPERAÇÃO DE SENHA (GMAIL & TELEFONE) --
@app.route('/auth/recover', methods=['POST'])
def recover_password():
    data = request.json
    identifier = data.get('email', '').strip()  # Front manda email ou telefone aqui

    # Tenta achar usuário por Email ou Telefone (limpo)
    clean_id = clean_input(identifier)

    user = User.query.filter(
        (User.email == identifier) |
        (User.phone == clean_id)
    ).first()

    if not user:
        # Retorna fake success para segurança
        return jsonify({"success": True, "msg": "Se os dados conferem, o código foi enviado."})

    # Gera código
    code = ''.join(random.choices(string.digits, k=6))
    expires = datetime.datetime.now() + datetime.timedelta(minutes=15)

    reset_entry = PasswordReset(user_id=user.id, code=code, expires_at=expires)
    db.session.add(reset_entry)
    db.session.commit()

    # LOGICA DE ENVIO (SIMULADA)
    # Aqui você integraria Twilio (SMS) ou SMTP (Email)
    # Por enquanto, mostramos no console do servidor
    print(f"========================================")
    print(f"🔐 RECUPERAÇÃO DE SENHA PARA: {user.username}")
    print(f"📧 Canal: {user.email} | 📱 {user.phone}")
    print(f"🔑 CÓDIGO: {code}")
    print(f"========================================")

    return jsonify({"success": True, "msg": "Código enviado! Verifique seu Email/SMS.", "debug_code": code})


@app.route('/auth/verify_code', methods=['POST'])
def verify_code():
    data = request.json
    reset = PasswordReset.query.filter_by(code=data['code'], used=False).first()
    if not reset or reset.expires_at < datetime.datetime.now():
        return jsonify({"success": False, "msg": "Código inválido ou expirado"})
    return jsonify({"success": True, "reset_id": reset.id})


@app.route('/auth/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    reset = PasswordReset.query.get(data['reset_id'])
    if not reset or reset.used:
        return jsonify({"success": False, "msg": "Solicitação inválida"})

    user = User.query.get(reset.user_id)
    user.password = data['new_password']
    reset.used = True
    db.session.commit()
    return jsonify({"success": True, "msg": "Senha alterada com sucesso!"})


# --- STATUS DO SISTEMA ---
@app.route('/system/status', methods=['GET'])
def get_system_status():
    s = SystemStatus.query.first()
    return jsonify({
        "invest": s.active_invest,
        "double": s.active_double,
        "mines": s.active_mines,
        "aviator": s.active_aviator,
        "status": "online"
    })


# --- PAGAMENTOS (MERCADO PAGO REAL) ---

@app.route('/deposit/pix', methods=['POST'])
def create_pix_deposit():
    data = request.json
    user_id = data.get('user_id')
    amount = float(data.get('amount'))

    user = User.query.get(user_id)
    if not user: return jsonify({"erro": True, "msg": "Usuário não encontrado"}), 404

    # --- NOVAS REGRAS DE LIMITES ---
    if amount < 20:
        return jsonify({"erro": True, "msg": "O depósito mínimo é de R$ 20,00"}), 400

    if amount > 3000:
        return jsonify({"erro": True, "msg": "O depósito máximo é de R$ 3.000,00 por vez"}), 400
    # -------------------------------

    try:
        payment_data = {
            "transaction_amount": amount,
            "description": f"Recarga Nexus - {user.username}",
            "payment_method_id": "pix",
            "payer": {
                "email": user.email,
                "first_name": user.username,
                "identification": {
                    "type": "CPF",
                    "number": user.cpf if user.cpf else "00000000000"  # Fallback
                }
            }
        }

        payment_response = sdk.payment().create(payment_data)
        payment = payment_response["response"]

        if payment["status"] == 400:
            return jsonify({"erro": True, "msg": "Erro nos dados (Verifique CPF/Email)"}), 400

        # Salvar depósito pendente no BD
        new_dep = Deposit(
            user_id=user.id,
            payment_id_mp=str(payment["id"]),
            amount=amount,
            status='pending'
        )
        db.session.add(new_dep)
        db.session.commit()

        # Dados para o Front
        qr_code = payment["point_of_interaction"]["transaction_data"]["qr_code"]
        qr_img = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]

        return jsonify({
            "success": True,
            "payment_id": payment["id"],
            "qr_code": qr_code,  # Copia e Cola
            "qr_base64": qr_img  # Imagem
        })

    except Exception as e:
        print("ERRO MP:", e)
        return jsonify({"erro": True, "msg": "Erro ao comunicar com Mercado Pago"}), 500


@app.route('/deposit/check', methods=['POST'])
def check_deposit_status():
    """Verifica se pagou e libera saldo"""
    data = request.json
    payment_id = data.get('payment_id')

    # Busca no nosso banco
    deposit = Deposit.query.filter_by(payment_id_mp=str(payment_id)).first()
    if not deposit:
        return jsonify({"success": False, "msg": "Depósito não encontrado"})

    if deposit.status == 'approved':
        return jsonify({"success": True, "status": "approved", "msg": "Já aprovado!"})

    # Consulta Mercado Pago
    try:
        mp_res = sdk.payment().get(int(payment_id))
        mp_status = mp_res["response"]["status"]

        if mp_status == 'approved':
            # ATUALIZA SALDO (CRÍTICO)
            user = User.query.get(deposit.user_id)
            user.balance += deposit.amount

            deposit.status = 'approved'
            registrar_log('deposito', deposit.amount, f"PIX Aprovado - {user.username}")

            db.session.commit()
            return jsonify({"success": True, "status": "approved", "new_balance": user.balance})

        return jsonify({"success": True, "status": mp_status})  # pending ou rejected

    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})


@app.route('/solicitar_saque', methods=['POST'])
def solicitar_saque():
    data = request.json
    user = User.query.get(data['user_id'])
    amount = float(data['amount'])

    if amount <= 0: return jsonify({"success": False, "msg": "Valor inválido."})
    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente."})

    user.balance -= amount
    wd = Withdrawal(user_id=user.id, username=user.username, amount=amount, pix_key=data['pix'])
    db.session.add(wd)
    db.session.commit()
    return jsonify({"success": True, "msg": "Solicitação enviada."})


# --- JOGOS (COM VALIDAÇÃO ANTI-NEGATIVO) ---

@app.route('/game/config', methods=['GET'])
def get_game_config():
    cfg = GameConfig.query.first()
    return jsonify({
        "chances": {"black": cfg.chance_black, "red": cfg.chance_red, "white": cfg.chance_white},
        "payouts": {"black": cfg.mult_black, "red": cfg.mult_red, "white": cfg.mult_white}
    })


@app.route('/game/spin', methods=['POST'])
def spin_game():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])
    color = data['bet_color']

    if bet <= 0: return jsonify({"success": False, "msg": "Aposta inválida"})
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet

    cfg = GameConfig.query.first()
    total = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, total)

    res_color = "white"
    if r < cfg.chance_black:
        res_color = "black"
    elif r < cfg.chance_black + cfg.chance_red:
        res_color = "red"

    win = (res_color == color)
    win_amt = 0
    if win:
        mult = cfg.mult_white if res_color == 'white' else (cfg.mult_black if res_color == 'black' else cfg.mult_red)
        win_amt = bet * mult
        user.balance += win_amt

    db.session.commit()
    return jsonify(
        {"success": True, "result_color": res_color, "win": win, "win_amount": win_amt, "new_balance": user.balance})


@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    if check_maintenance('aviator'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])

    if bet <= 0: return jsonify({"success": False, "msg": "Aposta inválida"})
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet

    cfg = GameConfig.query.first()
    # --- NOVA LÓGICA DE CRASH ---
    crash = 1.00

    # 1. Verifica se o Botão de Pânico foi ativado (Force Crash)
    if cfg.force_crash_rounds > 0:
        # Força queda entre 1.00x e 1.30x
        crash = round(random.uniform(1.00, 1.30), 2)
        cfg.force_crash_rounds -= 1  # Desconta uma rodada forçada
    else:
        # 2. Lógica baseada nas suas porcentagens
        r = random.uniform(0, 100)

        limit_low = cfg.aviator_prob_low  # Ex: 60
        limit_med = limit_low + cfg.aviator_prob_med  # Ex: 60+25 = 85
        limit_high = limit_med + cfg.aviator_prob_high  # Ex: 85+10 = 95

        if r < limit_low:
            # Faixa Baixa: 1.00x a 1.49x
            crash = random.uniform(1.00, 1.49)
        elif r < limit_med:
            # Faixa Média: 1.50x a 1.99x
            crash = random.uniform(1.50, 1.99)
        elif r < limit_high:
            # Faixa Alta: 2.00x a 4.99x
            crash = random.uniform(2.00, 4.99)
        else:
            # Faixa Jackpot: 5.00x até o Teto Máximo
            crash = random.uniform(5.00, cfg.aviator_max_mult)

        crash = round(crash, 2)

    db.session.commit()
    return jsonify({"success": True, "crash_point": crash, "new_balance": user.balance})


# 1. Rota para ativar o Force Crash (Botão de Pânico)
@app.route('/admin/force_crash', methods=['POST'])
def force_crash():
    cfg = GameConfig.query.first()
    cfg.force_crash_rounds = 3  # Define 3 rodadas de queda forçada
    db.session.commit()
    return jsonify({"success": True})


# 2. Rota para Excluir Usuário
@app.route('/admin/delete_user/<int:id>', methods=['DELETE'])
def delete_user(id):
    # Remove dependências primeiro para não dar erro de chave estrangeira
    Investment.query.filter_by(user_id=id).delete()
    Deposit.query.filter_by(user_id=id).delete()
    Withdrawal.query.filter_by(user_id=id).delete()
    FinancialLog.query.filter_by(user_id=id).delete()  # Se tiver relação user_id

    User.query.filter_by(id=id).delete()
    db.session.commit()
    return jsonify({"success": True})


@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    data = request.json
    user = User.query.get(data['user_id'])
    win = float(data['win_amount'])

    # Validação simples para evitar injeção direta
    if win <= 0: return jsonify({"success": False})

    user.balance += win
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})


@app.route('/game/double/place_bet', methods=['POST'])
def double_place_bet():
    global double_lobby
    data = request.json
    # Os bots enviam 'is_bot': True, então os ignoramos no cálculo de lucro
    if not data.get('is_bot'):
        color = data['color']
        amount = float(data['amount'])
        double_lobby["bets"][color] += amount

    return jsonify({"success": True})


@app.route('/game/double/get_result', methods=['GET'])
def double_get_result():
    global double_lobby

    # Lógica de Menor Pagamento (House Edge)
    # Payouts: Vermelho (2x), Preto (2x), Branco (14x)
    payouts = {
        "red": double_lobby["bets"]["red"] * 2,
        "black": double_lobby["bets"]["black"] * 2,
        "white": double_lobby["bets"]["white"] * 14
    }

    # A cor vencedora é aquela que exige o menor pagamento de volta aos players reais
    result_color = min(payouts, key=payouts.get)

    # Se houver empate em zero (ninguém apostou), sorteia aleatório
    if all(v == 0 for v in payouts.values()):
        import random
        result_color = random.choices(['red', 'black', 'white'], weights=[45, 45, 10])[0]

    # Reseta o lobby para a próxima rodada
    double_lobby = {"bets": {"red": 0.0, "black": 0.0, "white": 0.0}, "players": []}

    return jsonify({"result_color": result_color})


@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    if check_maintenance('mines'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])

    if bet <= 0: return jsonify({"success": False, "msg": "Aposta inválida"})
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet

    cfg = GameConfig.query.first()
    rigged = (random.uniform(0, 100) < cfg.mines_edge)

    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance, "rigged": rigged})


@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user = User.query.get(data['user_id'])
    win = float(data['win_amount'])

    if win <= 0: return jsonify({"success": False})

    user.balance += win
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})


# --- ROTAS DE USUÁRIO E INVESTIMENTO ---

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"erro": True}), 404
    check_investments_status(user_id)
    return jsonify({
        "id": u.id, "balance": u.balance,
        "vip_level": u.vip_level, "username": u.username,
        "cpf": u.cpf, "email": u.email
    })


def check_investments_status(user_id):
    invs = Investment.query.filter_by(user_id=user_id, status='ativo').all()
    now = datetime.datetime.now()
    changed = False
    for i in invs:
        if now >= i.end_date:
            i.status = 'finalizado'
            changed = True
    if changed: db.session.commit()


@app.route('/plans', methods=['GET'])
def get_plans():
    plans = Plan.query.all()
    return jsonify(
        [{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in
         plans])


@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    plan = Plan.query.get(data['plan_id'])
    amount = float(data['amount'])

    if amount <= 0: return jsonify({"success": False, "msg": "Valor inválido"})
    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    final_return = amount + (amount * plan.total_rate)
    end_date = datetime.datetime.now() + datetime.timedelta(minutes=plan.duration_minutes)

    inv = Investment(user_id=user.id, plan_name=plan.name, amount=amount, end_date=end_date, final_return=final_return)
    user.balance -= amount
    db.session.add(inv)
    db.session.commit()
    return jsonify({"success": True})


@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def meus_investimentos(user_id):
    check_investments_status(user_id)
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.start_date.desc()).all()
    return jsonify([{
        "id": i.id, "plan": i.plan_name, "amount": i.amount, "final_return": i.final_return,
        "start_ts": i.start_date.timestamp() * 1000, "end_ts": i.end_date.timestamp() * 1000, "status": i.status
    } for i in invs])


@app.route('/invest/withdraw_profit', methods=['POST'])
def withdraw_invest_profit():
    data = request.json
    inv = Investment.query.get(data['invest_id'])
    if not inv or inv.status == 'pago' or datetime.datetime.now() < inv.end_date:
        return jsonify({"success": False, "msg": "Erro ao sacar"})

    user = User.query.get(inv.user_id)
    user.balance += inv.final_return
    inv.status = 'pago'
    db.session.commit()
    return jsonify({"success": True, "amount": inv.final_return})


# --- ADMIN API (RESUMIDA) ---

@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403


@app.route('/admin/data', methods=['GET'])
def admin_data():
    users = [
        {"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level, "cpf": u.cpf, "phone": u.phone}
        for u in User.query.all()]
    plans = [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p
             in Plan.query.all()]
    withdrawals = [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status,
                    "date": w.date.strftime('%Y-%m-%d %H:%M')} for w in
                   Withdrawal.query.filter_by(status='pendente').all()]
    cfg = GameConfig.query.first()
    sys = SystemStatus.query.first()
    return jsonify({
        "users": users, "plans": plans, "withdrawals": withdrawals,
        "game": {"c_black": cfg.chance_black, "c_red": cfg.chance_red, "c_white": cfg.chance_white,
                 "m_black": cfg.mult_black, "m_red": cfg.mult_red, "m_white": cfg.mult_white,
                 "mines_edge": cfg.mines_edge, "aviator_edge": cfg.aviator_edge, "aviator_max": cfg.aviator_max_mult},
        "system": {"active_invest": sys.active_invest, "active_double": sys.active_double,
                   "active_mines": sys.active_mines, "active_aviator": sys.active_aviator}
    })


@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    data = request.json
    s = SystemStatus.query.first()
    t, v = data['type'], data['val']
    if t == 'invest': s.active_invest = v
    if t == 'double': s.active_double = v
    if t == 'mines': s.active_mines = v
    if t == 'aviator': s.active_aviator = v
    db.session.commit()
    return jsonify({"success": True})


@app.route('/admin/save_game_config', methods=['POST'])
def save_game_config():
    data = request.json
    cfg = GameConfig.query.first()
    if 'c_black' in data: cfg.chance_black = float(data['c_black'])
    if 'c_red' in data: cfg.chance_red = float(data['c_red'])
    if 'c_white' in data: cfg.chance_white = float(data['c_white'])
    if 'm_black' in data: cfg.mult_black = float(data['m_black'])
    if 'm_red' in data: cfg.mult_red = float(data['m_red'])
    if 'm_white' in data: cfg.mult_white = float(data['m_white'])
    if 'mines_edge' in data: cfg.mines_edge = float(data['mines_edge'])
    if 'aviator_edge' in data: cfg.aviator_edge = float(data['aviator_edge'])
    if 'aviator_max' in data: cfg.aviator_max_mult = float(data['aviator_max'])
    if 'aviator_prob_low' in data: cfg.aviator_prob_low = float(data['aviator_prob_low'])
    if 'aviator_prob_med' in data: cfg.aviator_prob_med = float(data['aviator_prob_med'])
    if 'aviator_prob_high' in data: cfg.aviator_prob_high = float(data['aviator_prob_high'])

    db.session.commit()
    return jsonify({"success": True})


@app.route('/admin/save_plan', methods=['POST'])
def save_plan():
    data = request.json
    if 'id' in data and data['id']:
        p = Plan.query.get(data['id'])
    else:
        p = Plan(); db.session.add(p)
    p.name = data['name'];
    p.duration_minutes = int(data['minutes']);
    p.total_rate = float(data['rate']);
    p.min_entry = float(data['min'])
    db.session.commit()
    return jsonify({"success": True})


@app.route('/admin/delete_plan/<int:id>', methods=['DELETE'])
def delete_plan(id):
    Plan.query.filter_by(id=id).delete()
    db.session.commit()
    return jsonify({"success": True})


@app.route('/admin/user_action', methods=['POST'])
def user_action():
    data = request.json
    u = User.query.get(data['id'])
    if 'vip' in data: u.vip_level = data['vip']
    if 'balance' in data: u.balance += float(data['balance'])
    db.session.commit()
    return jsonify({"success": True})


@app.route('/admin/withdrawal_action', methods=['POST'])
def withdrawal_action():
    data = request.json
    wd = Withdrawal.query.get(data['id'])
    if wd.status != 'pendente': return jsonify({"success": False})
    if data['action'] == 'approve':
        wd.status = 'aprovado'
    elif data['action'] == 'reject':
        wd.status = 'rejeitado'
        User.query.get(wd.user_id).balance += wd.amount
    db.session.commit()
    return jsonify({"success": True})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
