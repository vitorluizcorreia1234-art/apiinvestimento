import os
import datetime
import random
import time
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import mercadopago

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///nexus_pro.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get("SECRET_KEY", "nexus_master_key_v3_secure")

db = SQLAlchemy(app)
CORS(app)

# MP SDK
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "SEU_TOKEN_AQUI")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
ADMIN_PIN = "1234"

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')
    phone = db.Column(db.String(20))
    cpf = db.Column(db.String(20))
    reset_token = db.Column(db.String(10))
    # Segurança
    is_blocked = db.Column(db.Boolean, default=False)
    last_ip = db.Column(db.String(50))

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    mp_id = db.Column(db.String(50))
    amount = db.Column(db.Float)
    status = db.Column(db.String(20))

class Investment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    plan_name = db.Column(db.String(50))
    amount = db.Column(db.Float)
    start_date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    end_date = db.Column(db.DateTime)
    total_rate = db.Column(db.Float) # Taxa total (ex: 0.10 para 10%)
    final_return = db.Column(db.Float)
    status = db.Column(db.String(20), default='ativo')

class GameSession(db.Model):
    """Controla o estado do Mines/Aviator para evitar fraude no frontend"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    game = db.Column(db.String(20)) # 'mines', 'aviator'
    bet_amount = db.Column(db.Float)
    state_data = db.Column(db.Text) # JSON string com onde estão as bombas ou crash point
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class GlobalConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Double
    double_rtp = db.Column(db.Float, default=0.90) # House edge
    # Mines
    mines_force_loss = db.Column(db.Boolean, default=False)
    # Aviator
    aviator_crash_point = db.Column(db.Float, default=0.0) # Se > 1, força crash
    aviator_rtp = db.Column(db.Float, default=0.95)
    
    maintenance = db.Column(db.Boolean, default=False)

class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount = db.Column(db.Float)
    pix = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pendente')

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    duration_minutes = db.Column(db.Integer)
    total_rate = db.Column(db.Float)
    min_entry = db.Column(db.Float)

class AdminLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    msg = db.Column(db.String(255))
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# --- INICIALIZAÇÃO ---
with app.app_context():
    db.create_all()
    if not GlobalConfig.query.first():
        db.session.add(GlobalConfig())
    if not Plan.query.first():
        db.session.add(Plan(name="Turbo 24h", duration_minutes=1440, total_rate=0.05, min_entry=50))
    db.session.commit()

# --- HELPERS ---
def log_system(msg):
    db.session.add(AdminLog(msg=msg))
    db.session.commit()

def get_config():
    return GlobalConfig.query.first()

# --- ROTAS GERAIS ---
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "msg": "Nexus Server Online"})

@app.route('/login', methods=['POST'])
def login():
    d = request.json
    u = User.query.filter((User.username==d['login']) | (User.email==d['login']) | (User.cpf==d['login'])).first()
    if u and check_password_hash(u.password, d['password']):
        if u.is_blocked: return jsonify({"erro":True, "msg":"Conta bloqueada pelo suporte."}), 403
        return jsonify({"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level})
    return jsonify({"erro":True, "msg":"Dados incorretos"}), 401

@app.route('/register', methods=['POST'])
def register():
    d = request.json
    if User.query.filter_by(username=d['username']).first():
        return jsonify({"erro":True, "msg":"Usuário já existe"}), 400
    hashed = generate_password_hash(d['password'])
    u = User(username=d['username'], email=d.get('email'), cpf=d.get('cpf'), phone=d.get('phone'), password=hashed)
    db.session.add(u)
    db.session.commit()
    return jsonify({"success":True})

@app.route('/user/<int:uid>')
def get_user(uid):
    u = User.query.get(uid)
    # Check investimentos
    invs = Investment.query.filter_by(user_id=uid, status='ativo').all()
    now = datetime.datetime.utcnow()
    changed = False
    for i in invs:
        if now >= i.end_date:
            u.balance += i.final_return
            i.status = 'pago'
            changed = True
    if changed: db.session.commit()
    return jsonify({"id":u.id, "username":u.username, "balance":u.balance, "email":u.email, "vip":u.vip_level})

# --- GAMES: DOUBLE ---
@app.route('/game/double/spin', methods=['POST'])
def double_spin():
    cfg = get_config()
    if cfg.maintenance: return jsonify({"erro":True, "msg":"Manutenção"})
    
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['amount'])
    color = d['color'] # red, black, white

    if u.balance < bet: return jsonify({"erro":True, "msg":"Saldo insuficiente"})
    
    # Lógica RTP
    roll = random.uniform(0, 100)
    result = "black"
    # RTP Configuration (Simplificado)
    # Se RTP for baixo (ex 0.1), chance de branco diminui e chance da cor oposta aumenta
    if roll < 45: result = "red"
    elif roll > 90: result = "white"

    win = (color == result)
    mult = 14.0 if result == "white" else 2.0
    u.balance -= bet
    win_amount = 0
    
    if win:
        win_amount = bet * mult
        u.balance += win_amount
        log_system(f"User {u.username} ganhou {win_amount} no Double")
    
    db.session.commit()
    return jsonify({"success":True, "result": result, "win": win, "balance": u.balance, "win_amount": win_amount})

# --- GAMES: MINES PRO (Lógica Server-Side Segura) ---
@app.route('/game/mines/start', methods=['POST'])
def mines_start():
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['amount'])
    mines_count = int(d['mines'])
    
    if u.balance < bet: return jsonify({"erro":True, "msg":"Saldo insuficiente"})
    if mines_count < 1 or mines_count > 24: return jsonify({"erro":True, "msg":"Minas inválidas"})

    u.balance -= bet
    
    # Gerar bombas
    bombs = random.sample(range(25), mines_count)
    
    # Criar sessão
    session = GameSession(user_id=u.id, game='mines', bet_amount=bet, active=True, 
                          state_data=json.dumps({"bombs": bombs, "revealed": [], "mines_count": mines_count}))
    db.session.add(session)
    db.session.commit()
    
    return jsonify({"success":True, "game_id": session.id, "balance": u.balance})

@app.route('/game/mines/reveal', methods=['POST'])
def mines_reveal():
    d = request.json
    gs = GameSession.query.get(d['game_id'])
    cfg = get_config()
    
    if not gs or not gs.active: return jsonify({"erro":True, "msg":"Jogo inválido"})
    
    state = json.loads(gs.state_data)
    tile = int(d['tile'])
    
    # Manipulação Admin
    is_bomb = tile in state['bombs']
    if cfg.mines_force_loss:
        is_bomb = True # Força perda
    
    if is_bomb:
        gs.active = False
        db.session.commit()
        return jsonify({"status": "bomb", "bombs": state['bombs']})
    else:
        if tile not in state['revealed']:
            state['revealed'].append(tile)
            gs.state_data = json.dumps(state)
            db.session.commit()
        
        # Calcular Multiplicador
        # (Lógica simples de fatorial para odds justas + house edge)
        # Para simplificar o codigo, usaremos uma progressão padrão
        safe_steps = len(state['revealed'])
        # Multiplicador exponencial simples para exemplo
        mult = 1.0 + (safe_steps * (state['mines_count'] / 5.0)) 
        
        return jsonify({"status": "safe", "multiplier": mult})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    d = request.json
    gs = GameSession.query.get(d['game_id'])
    if not gs or not gs.active: return jsonify({"erro":True, "msg":"Jogo inválido"})
    
    state = json.loads(gs.state_data)
    safe_steps = len(state['revealed'])
    if safe_steps == 0: return jsonify({"erro":True, "msg":"Revele algo primeiro"})

    mult = 1.0 + (safe_steps * (state['mines_count'] / 5.0))
    win_val = gs.bet_amount * mult
    
    u = User.query.get(gs.user_id)
    u.balance += win_val
    gs.active = False
    
    log_system(f"User {u.username} sacou {win_val} no Mines (x{mult:.2f})")
    db.session.commit()
    return jsonify({"success":True, "amount": win_val, "balance": u.balance})

# --- GAMES: AVIATOR PRO ---
@app.route('/game/aviator/bet', methods=['POST'])
def aviator_bet():
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['amount'])
    
    if u.balance < bet: return jsonify({"erro":True, "msg":"Saldo insuficiente"})
    
    u.balance -= bet
    
    # Determinar Crash Point agora (Server Side Authority)
    cfg = get_config()
    crash_point = 0
    
    if cfg.aviator_crash_point > 1.0:
        crash_point = cfg.aviator_crash_point # Admin forçou valor
    else:
        # Algoritmo padrão de crash
        # E = 0.99 (RTP)
        e = 2 ** 32
        h = int(str(random.random())[2:11]) 
        if h % 33 == 0:
            crash_point = 1.00 # Instant Crash
        else:
            crash_point = max(1.00, (100 * e - h) / (e - h) / 100.0)
            # Aplicar RTP global
            if random.random() > cfg.aviator_rtp:
                crash_point = min(crash_point, 1.20) # Força crash cedo se RTP pedir

    session = GameSession(user_id=u.id, game='aviator', bet_amount=bet, active=True,
                          state_data=json.dumps({"crash_point": crash_point, "start_time": time.time()}))
    db.session.add(session)
    db.session.commit()
    
    return jsonify({"success":True, "game_id": session.id, "balance": u.balance})

@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    d = request.json
    gs = GameSession.query.get(d['game_id'])
    current_mult = float(d['current_mult']) # O cliente diz onde parou, mas validamos
    
    if not gs or not gs.active: return jsonify({"erro":True, "msg":"Jogo já finalizado"})
    
    state = json.loads(gs.state_data)
    real_crash = state['crash_point']
    
    # Validação de segurança: Se o user tentar sacar acima do crash point real
    if current_mult >= real_crash:
        gs.active = False
        db.session.commit()
        return jsonify({"success":False, "msg": "Plane Crashed", "crashed_at": real_crash})
    
    win = gs.bet_amount * current_mult
    u = User.query.get(gs.user_id)
    u.balance += win
    gs.active = False
    
    log_system(f"User {u.username} sacou Aviator x{current_mult} -> {win}")
    db.session.commit()
    return jsonify({"success":True, "win": win, "balance": u.balance})

@app.route('/game/aviator/status', methods=['GET'])
def aviator_status():
    """Rota para o frontend saber o crash point (somente para simulação visual sync se fosse multiplayer real)"""
    # Em modo Single Player Simulado, o endpoint Bet já retorna o necessário.
    return jsonify({"active": True})

# --- INVESTIMENTOS ---
@app.route('/plans', methods=['GET'])
def plans():
    return jsonify([{"id":p.id, "name":p.name, "min":p.min_entry, "minutes":p.duration_minutes, "rate":p.total_rate} for p in Plan.query.all()])

@app.route('/invest/create', methods=['POST'])
def invest_create():
    d = request.json
    u = User.query.get(d['user_id'])
    p = Plan.query.get(d['plan_id'])
    amt = float(d['amount'])
    
    if u.balance < amt: return jsonify({"erro":True, "msg":"Saldo insuficiente"})
    if amt < p.min_entry: return jsonify({"erro":True, "msg":f"Mínimo {p.min_entry}"})
    
    u.balance -= amt
    # Calcula retorno
    final = amt + (amt * p.total_rate)
    end = datetime.datetime.utcnow() + datetime.timedelta(minutes=p.duration_minutes)
    
    inv = Investment(user_id=u.id, plan_name=p.name, amount=amt, end_date=end, total_rate=p.total_rate, final_return=final)
    db.session.add(inv)
    db.session.commit()
    return jsonify({"success":True, "msg":"Investimento criado!"})

@app.route('/invest/my/<int:uid>')
def my_invest(uid):
    # Processa pagamentos primeiro
    get_user(uid) 
    invs = Investment.query.filter_by(user_id=uid).order_by(Investment.id.desc()).all()
    res = []
    now = datetime.datetime.utcnow()
    for i in invs:
        # Calculo de progresso para o frontend
        total_time = (i.end_date - i.start_date).total_seconds()
        elapsed = (now - i.start_date).total_seconds()
        pct = min(100, (elapsed / total_time) * 100) if total_time > 0 else 100
        
        res.append({
            "id": i.id,
            "plan": i.plan_name,
            "amount": i.amount,
            "final_return": i.final_return,
            "start_ts": i.start_date.timestamp(),
            "end_ts": i.end_date.timestamp(),
            "status": i.status,
            "pct": pct
        })
    return jsonify(res)

# --- SAQUE ---
@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    u = User.query.get(d['user_id'])
    amt = float(d['amount'])
    if u.balance < amt: return jsonify({"erro":True, "msg":"Saldo insuficiente"})
    
    u.balance -= amt
    w = Withdrawal(user_id=u.id, amount=amt, pix=d['pix'])
    db.session.add(w)
    log_system(f"Solicitação saque: {u.username} - R$ {amt}")
    db.session.commit()
    return jsonify({"success":True, "msg":"Solicitado com sucesso!"})

# --- ADMIN API ---
@app.route('/admin/data', methods=['POST'])
def admin_data():
    if request.json.get('pin') != ADMIN_PIN: return jsonify({"erro":True}), 403
    
    users = [{"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level, "blocked":u.is_blocked} for u in User.query.all()]
    logs = [{"date":l.date.strftime("%d/%m %H:%M"), "msg":l.msg} for l in AdminLog.query.order_by(AdminLog.id.desc()).limit(50).all()]
    cfg = get_config()
    
    return jsonify({
        "users": users,
        "logs": logs,
        "config": {
            "mines_force": cfg.mines_force_loss,
            "aviator_force": cfg.aviator_crash_point,
            "rtp": cfg.aviator_rtp
        }
    })

@app.route('/admin/action', methods=['POST'])
def admin_action():
    if request.json.get('pin') != ADMIN_PIN: return jsonify({"erro":True}), 403
    d = request.json
    action = d['action']
    
    if action == 'toggle_block':
        u = User.query.get(d['uid'])
        u.is_blocked = not u.is_blocked
    
    elif action == 'money':
        u = User.query.get(d['uid'])
        val = float(d['val'])
        u.balance += val # Se val for negativo, subtrai
    
    elif action == 'config_mines':
        c = get_config()
        c.mines_force_loss = d['val']
        
    elif action == 'config_aviator':
        c = get_config()
        c.aviator_crash_point = float(d['val']) # Se setar 1.0, crasha instantaneo
        
    db.session.commit()
    return jsonify({"success":True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
