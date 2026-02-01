from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
import random
import time
import string

app = Flask(__name__)

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    db_url = "sqlite:///nexus.db"

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "nexus_ultra_secure_key_v3"

db = SQLAlchemy(app)
CORS(app)

ADMIN_PIN = "1234"

# --- MODELOS (TABELAS) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    # Novos Campos Blindados
    cpf = db.Column(db.String(14), unique=True, nullable=True) # Nullable para suportar users antigos se precisar
    phone = db.Column(db.String(20), nullable=True)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')

class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

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
    # Double
    mult_black = db.Column(db.Float, default=2.0)
    mult_red = db.Column(db.Float, default=2.0)
    mult_white = db.Column(db.Float, default=14.0)
    chance_black = db.Column(db.Float, default=45.0)
    chance_red = db.Column(db.Float, default=45.0)
    chance_white = db.Column(db.Float, default=10.0)
    # Mines & Aviator Risk
    mines_edge = db.Column(db.Float, default=30.0)
    aviator_max_mult = db.Column(db.Float, default=10.0)
    aviator_edge = db.Column(db.Float, default=10.0)

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

# --- ROTAS DE AUTH (LOGIN/REGISTRO/RECUPERAÇÃO) ---

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    # Permite login por username, email ou cpf (se implementado no front)
    user = User.query.filter((User.username == data.get('login')) | (User.email == data.get('login'))).first()
    
    if user and user.password == data.get('password'):
        return jsonify({
            "id": user.id, 
            "username": user.username, 
            "balance": user.balance, 
            "vip_level": user.vip_level
        })
    return jsonify({"erro": True, "msg": "Credenciais inválidas"}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    
    # Validações Básicas
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"erro": True, "msg": "Usuário já existe"}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({"erro": True, "msg": "Email já cadastrado"}), 400
    
    # CPF e Telefone (Opcionais no BD antigo, Obrigatórios agora)
    cpf = data.get('cpf', '').replace('.', '').replace('-', '')
    phone = data.get('phone', '')

    if cpf and User.query.filter_by(cpf=cpf).first():
        return jsonify({"erro": True, "msg": "CPF já cadastrado"}), 400
        
    new_user = User(
        username=data['username'], 
        email=data['email'], 
        password=data['password'],
        cpf=cpf,
        phone=phone
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

# -- RECUPERAÇÃO DE SENHA --
@app.route('/auth/recover', methods=['POST'])
def recover_password():
    data = request.json
    identifier = data.get('email') # Pode ser email ou telefone
    user = User.query.filter((User.email == identifier) | (User.phone == identifier)).first()
    
    if not user:
        # Retorna sucesso fake para não expor quais emails existem
        return jsonify({"success": True, "msg": "Se os dados existirem, o código foi enviado."})
    
    # Gera código de 6 dígitos
    code = ''.join(random.choices(string.digits, k=6))
    expires = datetime.datetime.now() + datetime.timedelta(minutes=15)
    
    reset_entry = PasswordReset(user_id=user.id, code=code, expires_at=expires)
    db.session.add(reset_entry)
    db.session.commit()
    
    # EM PRODUÇÃO: Enviar SMS ou Email aqui.
    # PARA TESTE: Retornamos o código no JSON (Simulação)
    print(f"CODIGO DE RECUPERAÇÃO PARA {user.username}: {code}")
    return jsonify({"success": True, "msg": "Código enviado!", "debug_code": code})

@app.route('/auth/verify_code', methods=['POST'])
def verify_code():
    data = request.json
    # Busca o código válido mais recente
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

# --- ROTAS DE USUÁRIO E INVESTIMENTO ---
# (Mantidas iguais, apenas garantindo que as tabelas novas não quebrem nada)

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
    return jsonify([{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in plans])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Investimentos em manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    plan = Plan.query.get(data['plan_id'])
    amount = float(data['amount'])
    
    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    final_return = amount + (amount * plan.total_rate)
    end_date = datetime.datetime.now() + datetime.timedelta(minutes=plan.duration_minutes)
    
    inv = Investment(user_id=user.id, plan_name=plan.name, amount=amount, end_date=end_date, final_return=final_return)
    user.balance -= amount
    registrar_log('entrada', amount, f"Investimento {plan.name} - {user.username}")
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
    registrar_log('saida', inv.final_return, f"Saque Investimento - {user.username}")
    db.session.commit()
    return jsonify({"success": True, "amount": inv.final_return})

@app.route('/solicitar_saque', methods=['POST'])
def solicitar_saque():
    data = request.json
    user = User.query.get(data['user_id'])
    amount = float(data['amount'])
    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente."})
    
    user.balance -= amount
    wd = Withdrawal(user_id=user.id, username=user.username, amount=amount, pix_key=data['pix'])
    db.session.add(wd)
    db.session.commit()
    return jsonify({"success": True, "msg": "Solicitação enviada."})

# --- GAMES (DOUBLE, AVIATOR, MINES) ---

@app.route('/game/config', methods=['GET'])
def get_game_config():
    cfg = GameConfig.query.first()
    return jsonify({
        "chances": {"black": cfg.chance_black, "red": cfg.chance_red, "white": cfg.chance_white},
        "payouts": {"black": cfg.mult_black, "red": cfg.mult_red, "white": cfg.mult_white}
    })

@app.route('/game/spin', methods=['POST'])
def spin_game():
    # DOUBLE
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Double em manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])
    color = data['bet_color']
    
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    user.balance -= bet
    registrar_log('entrada', bet, f"Aposta Double - {user.username}")
    
    cfg = GameConfig.query.first()
    total = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, total)
    
    res_color = "white"
    if r < cfg.chance_black: res_color = "black"
    elif r < cfg.chance_black + cfg.chance_red: res_color = "red"
    
    win = False
    win_amt = 0
    if res_color == color:
        win = True
        mult = cfg.mult_white if res_color == 'white' else (cfg.mult_black if res_color == 'black' else cfg.mult_red)
        win_amt = bet * mult
        user.balance += win_amt
        registrar_log('saida', win_amt, f"Vitoria Double - {user.username}")
        
    db.session.commit()
    return jsonify({"success": True, "result_color": res_color, "win": win, "win_amount": win_amt, "new_balance": user.balance})

@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    if check_maintenance('aviator'): return jsonify({"success": False, "msg": "Aviator em manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])
    
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    user.balance -= bet
    registrar_log('entrada', bet, f"Aposta Aviator - {user.username}")
    
    cfg = GameConfig.query.first()
    if random.uniform(0, 100) < cfg.aviator_edge:
        crash = 1.00
    else:
        x = random.uniform(1, 100)
        mult = 0.99 / (1 - (x / 100))
        if mult > cfg.aviator_max_mult: mult = cfg.aviator_max_mult
        crash = max(1.00, round(mult, 2))
        
    db.session.commit()
    return jsonify({"success": True, "crash_point": crash, "new_balance": user.balance})

@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    data = request.json
    user = User.query.get(data['user_id'])
    win = float(data['win_amount'])
    user.balance += win
    registrar_log('saida', win, f"Saque Aviator - {user.username}")
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})

@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    if check_maintenance('mines'): return jsonify({"success": False, "msg": "Mines em manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])
    
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    user.balance -= bet
    registrar_log('entrada', bet, f"Aposta Mines - {user.username}")
    
    cfg = GameConfig.query.first()
    rigged = False
    if random.uniform(0, 100) < cfg.mines_edge: rigged = True
    
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance, "rigged": rigged})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user = User.query.get(data['user_id'])
    win = float(data['win_amount'])
    user.balance += win
    registrar_log('saida', win, f"Saque Mines - {user.username}")
    db.session.commit()
    return jsonify({"success": True, "new_balance": user.balance})

# --- ADMIN API (GENÉRICA) ---
# As rotas admin_auth, admin_data, etc. continuam aqui, mas omiti para poupar espaço
# pois você já tem elas e não mudaram a lógica, apenas o conteúdo de dados (Users agora tem cpf/phone)
# Se precisar que eu reenvie o bloco admin inteiro, me avise.

@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403

@app.route('/admin/data', methods=['GET'])
def admin_data():
    # Inclui CPF e Phone na visualização do admin
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level, "cpf": u.cpf, "phone": u.phone} for u in User.query.all()]
    plans = [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()]
    withdrawals = [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status, "date": w.date.strftime('%Y-%m-%d %H:%M')} for w in Withdrawal.query.filter_by(status='pendente').all()]
    cfg = GameConfig.query.first()
    sys = SystemStatus.query.first()
    return jsonify({
        "users": users, "plans": plans, "withdrawals": withdrawals,
        "game": {"c_black": cfg.chance_black, "c_red": cfg.chance_red, "c_white": cfg.chance_white, "m_black": cfg.mult_black, "m_red": cfg.mult_red, "m_white": cfg.mult_white, "mines_edge": cfg.mines_edge, "aviator_edge": cfg.aviator_edge, "aviator_max": cfg.aviator_max_mult},
        "system": {"active_invest": sys.active_invest, "active_double": sys.active_double, "active_mines": sys.active_mines, "active_aviator": sys.active_aviator}
    })
# (Restante das rotas Admin: toggle_system, save_game_config, save_plan, delete_plan, user_action, withdrawal_action...
#  Elas não mudaram, apenas use as do código anterior se precisar colar tudo junto).
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
