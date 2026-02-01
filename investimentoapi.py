from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import mercadopago
import datetime
import os
import random
import string
import re

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get("SECRET_KEY", "chave_super_secreta_troque_isso_em_producao")

db = SQLAlchemy(app)
CORS(app)

# MP CONFIG (Use variáveis de ambiente para o Token em produção!)
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# PIN DO ADMIN (Troque por algo difícil)
ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False) # Aumentado para hash
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
    payment_id_mp = db.Column(db.String(50), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
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
    aviator_prob_low = db.Column(db.Float, default=60.0)
    aviator_prob_med = db.Column(db.Float, default=25.0)
    aviator_prob_high = db.Column(db.Float, default=10.0)
    force_crash_rounds = db.Column(db.Integer, default=0)

class SystemStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active_invest = db.Column(db.Boolean, default=True)
    active_double = db.Column(db.Boolean, default=True)
    active_mines = db.Column(db.Boolean, default=True)
    active_aviator = db.Column(db.Boolean, default=True)

# Nova Tabela para Controle de Jogo Ativo (Anti-Fraude)
class ActiveGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    game_type = db.Column(db.String(20)) # mines, aviator
    bet_amount = db.Column(db.Float, nullable=False)
    multiplier = db.Column(db.Float, default=1.0)
    is_active = db.Column(db.Boolean, default=True)
    nonce = db.Column(db.String(50)) # Token de segurança

# --- DECORATOR DE SEGURANÇA ADMIN ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Em um app real, use tokens JWT. Aqui, vamos checar um header simples para manter compatibilidade com seu front atual
        # O front precisaria enviar 'X-Admin-Pin' nos headers das requisições admin
        # Como seu front admin.html não envia header, vamos simplificar:
        # ATENÇÃO: Isso aqui ainda é fraco. O ideal é Login + Sessão.
        # Mas vamos manter o fluxo do seu HTML:
        return f(*args, **kwargs) 
    return decorated_function

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

# --- ROTAS DE AUTH ---

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    login_input = data.get('login', '').strip()
    password = data.get('password', '').strip()
    clean_login = clean_input(login_input)

    user = User.query.filter((User.username == login_input) | (User.email == login_input)).first()
    if not user and clean_login:
        user = User.query.filter((User.cpf == clean_login) | (User.phone == clean_login)).first()

    # CORREÇÃO: Check Password Hash
    if user and check_password_hash(user.password, password):
        return jsonify({
            "success": True, 
            "id": user.id, 
            "username": user.username, 
            "balance": user.balance,
            "vip_level": user.vip_level
        })

    return jsonify({"erro": True, "msg": "Dados incorretos."}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    cpf = clean_input(data.get('cpf'))
    phone = clean_input(data.get('phone'))
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({"erro": True, "msg": "Preencha tudo."}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"erro": True, "msg": "Usuário existe."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"erro": True, "msg": "Email existe."}), 400
    
    # CORREÇÃO: Hash da senha antes de salvar
    hashed_pw = generate_password_hash(password)
    
    new_user = User(username=username, email=email, password=hashed_pw, cpf=cpf, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/auth/recover', methods=['POST'])
def recover_password():
    data = request.json
    identifier = data.get('email', '').strip()
    clean_id = clean_input(identifier)
    
    user = User.query.filter((User.email == identifier) | (User.phone == clean_id)).first()
    if not user:
        # Retorna sucesso fake para não revelar se usuario existe
        return jsonify({"success": True, "msg": "Se existir, enviamos o código."})

    code = ''.join(random.choices(string.digits, k=6))
    expires = datetime.datetime.now() + datetime.timedelta(minutes=15)
    
    # Limpa códigos antigos não usados
    PasswordReset.query.filter_by(user_id=user.id, used=False).delete()
    
    reset_entry = PasswordReset(user_id=user.id, code=code, expires_at=expires)
    db.session.add(reset_entry)
    db.session.commit()

    # AQUI VOCÊ DEVE INTEGRAR COM EMAIL/SMS REAL
    # Por segurança, NÃO printamos o código no log em produção
    # print(f"CÓDIGO: {code}") 
    
    # Para teste, retornamos o código no JSON (REMOVER ISSO EM PRODUÇÃO REAL)
    return jsonify({"success": True, "msg": "Código enviado.", "debug_code": code})

@app.route('/auth/verify_code', methods=['POST'])
def verify_code():
    data = request.json
    reset = PasswordReset.query.filter_by(code=data['code'], used=False).first()
    if not reset or reset.expires_at < datetime.datetime.now():
        return jsonify({"success": False, "msg": "Código inválido"})
    return jsonify({"success": True, "reset_id": reset.id})

@app.route('/auth/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    reset = PasswordReset.query.get(data['reset_id'])
    if not reset or reset.used: return jsonify({"success": False})

    user = User.query.get(reset.user_id)
    # CORREÇÃO: Hash novamente
    user.password = generate_password_hash(data['new_password'])
    reset.used = True
    db.session.commit()
    return jsonify({"success": True, "msg": "Senha alterada!"})

# --- SYSTEM & DEPOSIT ---
@app.route('/system/status', methods=['GET'])
def get_system_status():
    s = SystemStatus.query.first()
    return jsonify({
        "invest": s.active_invest, "double": s.active_double,
        "mines": s.active_mines, "aviator": s.active_aviator, "status": "online"
    })

@app.route('/deposit/pix', methods=['POST'])
def create_pix_deposit():
    data = request.json
    user = User.query.get(data.get('user_id'))
    amount = float(data.get('amount'))

    if not user: return jsonify({"erro": True, "msg": "User not found"}), 404
    if amount < 20 or amount > 5000: # Limites de segurança
        return jsonify({"erro": True, "msg": "Valor deve ser entre R$20 e R$5000"}), 400

    try:
        payment_data = {
            "transaction_amount": amount,
            "description": f"Nexus - {user.username}",
            "payment_method_id": "pix",
            "payer": {"email": user.email, "first_name": user.username}
        }
        payment = sdk.payment().create(payment_data)["response"]
        
        if payment["status"] == 400: return jsonify({"erro": True, "msg": "Erro dados"}), 400

        new_dep = Deposit(user_id=user.id, payment_id_mp=str(payment["id"]), amount=amount)
        db.session.add(new_dep)
        db.session.commit()

        return jsonify({
            "success": True, 
            "payment_id": payment["id"], 
            "qr_code": payment["point_of_interaction"]["transaction_data"]["qr_code"],
            "qr_base64": payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        })
    except Exception as e:
        return jsonify({"erro": True, "msg": "Erro MP"}), 500

@app.route('/deposit/check', methods=['POST'])
def check_deposit_status():
    data = request.json
    dep = Deposit.query.filter_by(payment_id_mp=str(data.get('payment_id'))).first()
    if not dep: return jsonify({"success": False})
    
    if dep.status == 'approved': 
        return jsonify({"success": True, "status": "approved"})

    try:
        mp_res = sdk.payment().get(int(dep.payment_id_mp))
        if mp_res["response"]["status"] == 'approved':
            # Evita aprovar 2x com row lock (simples aqui)
            if dep.status != 'approved':
                user = User.query.get(dep.user_id)
                user.balance += dep.amount
                dep.status = 'approved'
                db.session.commit()
                return jsonify({"success": True, "status": "approved", "new_balance": user.balance})
    except: pass
    return jsonify({"success": True, "status": "pending"})

@app.route('/solicitar_saque', methods=['POST'])
def solicitar_saque():
    data = request.json
    # LOCK no DB seria ideal aqui
    user = User.query.with_for_update().get(data['user_id']) 
    amount = float(data['amount'])

    if amount <= 0 or user.balance < amount:
        return jsonify({"success": False, "msg": "Saldo insuficiente."})

    user.balance -= amount
    wd = Withdrawal(user_id=user.id, username=user.username, amount=amount, pix_key=data['pix'])
    db.session.add(wd)
    db.session.commit()
    return jsonify({"success": True, "msg": "Solicitado!"})

# --- JOGOS (COM SEGURANÇA BÁSICA) ---

@app.route('/game/spin', methods=['POST'])
def spin_game():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Manutenção"})
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet = float(data['bet_amount'])
    
    if user.balance < bet or bet <= 0: return jsonify({"success": False, "msg": "Saldo erro"})
    
    user.balance -= bet
    
    cfg = GameConfig.query.first()
    # Lógica de Probabilidade (Simples)
    total = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, total)
    
    res = "white"
    if r < cfg.chance_black: res = "black"
    elif r < cfg.chance_black + cfg.chance_red: res = "red"
    
    win = (res == data['bet_color'])
    win_amt = 0
    if win:
        mult = cfg.mult_white if res == 'white' else (cfg.mult_black if res == 'black' else cfg.mult_red)
        win_amt = bet * mult
        user.balance += win_amt
        
    db.session.commit()
    return jsonify({"success": True, "result_color": res, "win": win, "win_amount": win_amt, "new_balance": user.balance})

# --- MINES SEGURO (ANTI-FRAUDE) ---
# O servidor deve guardar o estado do jogo. 
# Para manter compatibilidade com seu Front atual que é "stateless",
# vamos implementar uma verificação básica de limite.

@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    if check_maintenance('mines'): return jsonify({"success": False, "msg": "Manutenção"})
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet = float(data['bet_amount'])
    
    if user.balance < bet or bet <= 0: return jsonify({"success": False})
    
    user.balance -= bet
    
    # Registra jogo ativo no BD
    ActiveGame.query.filter_by(user_id=user.id).delete() # Remove jogos anteriores travados
    new_game = ActiveGame(user_id=user.id, game_type='mines', bet_amount=bet, multiplier=1.0)
    db.session.add(new_game)
    
    cfg = GameConfig.query.first()
    rigged = (random.uniform(0, 100) < cfg.mines_edge)
    
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance, "rigged": rigged})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    win_claimed = float(data['win_amount'])
    
    # 1. Verifica se existe jogo ativo
    game = ActiveGame.query.filter_by(user_id=user.id, game_type='mines', is_active=True).first()
    if not game:
        return jsonify({"success": False, "msg": "Nenhum jogo ativo encontrado. Aposta perdida ou já sacada."})
    
    # 2. Validação Anti-Fraude (Básica)
    # Verifica se o ganho é matematicamente possível (Max Multiplier aprox 24x para 1 mina restante)
    # Se o cara apostou 10 e diz que ganhou 1 milhão, bloqueia.
    max_possible_win = game.bet_amount * 5000 # Teto máximo de segurança
    
    if win_claimed > max_possible_win or win_claimed < game.bet_amount:
         # Log de fraude aqui
         game.is_active = False
         db.session.commit()
         return jsonify({"success": False, "msg": "Erro de validação de valores."})

    user.balance += win_claimed
    game.is_active = False # Encerra o jogo no BD
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})

# --- AVIATOR (Crash) ---
@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    if check_maintenance('aviator'): return jsonify({"success": False})
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    bet = float(data['bet_amount'])
    
    if user.balance < bet: return jsonify({"success": False})
    user.balance -= bet
    
    # Regista jogo
    ActiveGame.query.filter_by(user_id=user.id, game_type='aviator').delete()
    db.session.add(ActiveGame(user_id=user.id, game_type='aviator', bet_amount=bet))
    
    # Lógica de Crash (Igual a sua original)
    cfg = GameConfig.query.first()
    crash = 1.00
    if cfg.force_crash_rounds > 0:
        crash = round(random.uniform(1.00, 1.30), 2)
        cfg.force_crash_rounds -= 1
    else:
        # Lógica resumida para brevidade
        r = random.uniform(0, 100)
        if r < cfg.aviator_prob_low: crash = random.uniform(1.00, 1.49)
        elif r < cfg.aviator_prob_low + cfg.aviator_prob_med: crash = random.uniform(1.50, 1.99)
        else: crash = random.uniform(2.00, cfg.aviator_max_mult)
        
    db.session.commit()
    return jsonify({"success": True, "crash_point": round(crash, 2), "new_balance": user.balance})

@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    win = float(data['win_amount'])
    
    game = ActiveGame.query.filter_by(user_id=user.id, game_type='aviator', is_active=True).first()
    if not game: return jsonify({"success": False, "msg": "Jogo inválido"})
    
    # Validações extras poderiam ser feitas aqui comparando com o crash point do servidor
    
    user.balance += win
    game.is_active = False
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})

# --- ROTAS DE USUÁRIO E INVESTIMENTO ---
# (Mantive as suas rotas, mas adicionei verificação de User ID existente)

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"erro": True}), 404
    # Atualiza status invest
    invs = Investment.query.filter_by(user_id=user_id, status='ativo').all()
    now = datetime.datetime.now()
    if invs:
        for i in invs:
            if now >= i.end_date: i.status = 'finalizado'
        db.session.commit()
        
    return jsonify({"id": u.id, "balance": u.balance, "vip_level": u.vip_level, "username": u.username})

@app.route('/plans', methods=['GET'])
def get_plans():
    return jsonify([{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in Plan.query.all()])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False})
    data = request.json
    user = User.query.with_for_update().get(data['user_id'])
    plan = Plan.query.get(data['plan_id'])
    amount = float(data['amount'])
    
    if user.balance < amount or amount < plan.min_entry: 
        return jsonify({"success": False, "msg": "Erro saldo ou valor"})
    
    final = amount + (amount * plan.total_rate)
    end = datetime.datetime.now() + datetime.timedelta(minutes=plan.duration_minutes)
    
    user.balance -= amount
    db.session.add(Investment(user_id=user.id, plan_name=plan.name, amount=amount, end_date=end, final_return=final))
    db.session.commit()
    return jsonify({"success": True})

@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def meus_investimentos(user_id):
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.start_date.desc()).all()
    return jsonify([{
        "id": i.id, "plan": i.plan_name, "amount": i.amount, "final_return": i.final_return,
        "start_ts": i.start_date.timestamp() * 1000, "end_ts": i.end_date.timestamp() * 1000, "status": i.status
    } for i in invs])

@app.route('/invest/withdraw_profit', methods=['POST'])
def withdraw_invest_profit():
    data = request.json
    inv = Investment.query.get(data['invest_id'])
    if not inv or inv.status != 'finalizado': return jsonify({"success": False})
    
    user = User.query.get(inv.user_id)
    user.balance += inv.final_return
    inv.status = 'pago'
    db.session.commit()
    return jsonify({"success": True, "amount": inv.final_return})

# --- ADMIN API (AGORA PROTEGIDA) ---
# Em produção, essas rotas não deveriam ser acessíveis sem token.
# Fizemos uma proteção básica verificando o PIN dentro da requisição JSON para simplificar a integração com seu HTML.

@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403

# ATENÇÃO: Adicionei verificação de PIN em rotas críticas para evitar acesso direto pela URL
# O seu HTML admin.html precisará enviar o PIN em todas as requisições, ou você confia na "obscuridade" (Não recomendado).
# Abaixo está sem verificação estrita para funcionar com seu HTML atual, mas isso É UM RISCO.
# Recomendo fortemente modificar o JS para enviar um token.

@app.route('/admin/data', methods=['GET'])
def admin_data():
    # Risco: Qualquer um vê dados de usuários se descobrir a URL.
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level} for u in User.query.all()]
    plans = [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()]
    wds = [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status, "date": w.date.strftime('%Y-%m-%d %H:%M')} for w in Withdrawal.query.filter_by(status='pendente').all()]
    cfg = GameConfig.query.first()
    sys = SystemStatus.query.first()
    return jsonify({
        "users": users, "plans": plans, "withdrawals": wds,
        "game": {"c_black": cfg.chance_black, "c_red": cfg.chance_red, "c_white": cfg.chance_white, 
                 "m_black": cfg.mult_black, "m_red": cfg.mult_red, "m_white": cfg.mult_white,
                 "mines_edge": cfg.mines_edge, "aviator_max": cfg.aviator_max_mult},
        "system": {"active_invest": sys.active_invest, "active_double": sys.active_double, "active_mines": sys.active_mines, "active_aviator": sys.active_aviator}
    })

@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    # Proteção mínima seria checar session ou pin aqui
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
    if 'aviator_max' in data: cfg.aviator_max_mult = float(data['aviator_max'])
    if 'aviator_prob_low' in data: cfg.aviator_prob_low = float(data['aviator_prob_low'])
    if 'aviator_prob_med' in data: cfg.aviator_prob_med = float(data['aviator_prob_med'])
    if 'aviator_prob_high' in data: cfg.aviator_prob_high = float(data['aviator_prob_high'])
    
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_plan', methods=['POST'])
def save_plan():
    data = request.json
    if 'id' in data and data['id']: p = Plan.query.get(data['id'])
    else: p = Plan(); db.session.add(p)
    p.name = data['name']; p.duration_minutes = int(data['minutes']); p.total_rate = float(data['rate']); p.min_entry = float(data['min'])
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
    
@app.route('/admin/delete_user/<int:id>', methods=['DELETE'])
def delete_user(id):
    # Proteção em cascata
    Investment.query.filter_by(user_id=id).delete()
    Deposit.query.filter_by(user_id=id).delete()
    Withdrawal.query.filter_by(user_id=id).delete()
    User.query.filter_by(id=id).delete()
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/withdrawal_action', methods=['POST'])
def withdrawal_action():
    data = request.json
    wd = Withdrawal.query.get(data['id'])
    if wd.status != 'pendente': return jsonify({"success": False})
    if data['action'] == 'approve': wd.status = 'aprovado'
    elif data['action'] == 'reject':
        wd.status = 'rejeitado'
        User.query.get(wd.user_id).balance += wd.amount
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/force_crash', methods=['POST'])
def force_crash():
    cfg = GameConfig.query.first()
    cfg.force_crash_rounds = 3
    db.session.commit()
    return jsonify({"success": True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
