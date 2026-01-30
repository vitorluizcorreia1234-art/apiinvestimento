import os
import datetime
import random
import time
import uuid
import re
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
app.secret_key = os.environ.get("SECRET_KEY", "nexus_god_mode_v3")

db = SQLAlchemy(app)
CORS(app)

MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "SEU_TOKEN_MP_AQUI")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
ADMIN_PIN = "1234"

# --- MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    cpf = db.Column(db.String(14), unique=True)
    password = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')
    restricted = db.Column(db.Boolean, default=False) # Se true, sempre perde

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Status dos Jogos
    active_double = db.Column(db.Boolean, default=True)
    active_mines = db.Column(db.Boolean, default=True)
    active_aviator = db.Column(db.Boolean, default=True)
    # Config Mines
    mines_house_edge = db.Column(db.Float, default=0.95) # 95% RTP
    mines_force_bomb = db.Column(db.Boolean, default=False) # Próximo clique é bomba
    # Config Aviator
    aviator_rtp = db.Column(db.Float, default=0.90)
    aviator_force_crash = db.Column(db.Float, default=0.0) # Se > 1, crasha nesse valor
    # Config Double
    double_rtp = db.Column(db.Float, default=0.90)

class GameLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(80))
    game = db.Column(db.String(50))
    action = db.Column(db.String(50)) # Win/Loss
    amount = db.Column(db.Float)
    details = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)

class MinesSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    bet = db.Column(db.Float)
    mines_count = db.Column(db.Integer)
    bombs_positions = db.Column(db.String(200)) # "1,5,9"
    revealed_count = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)

class Investment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    plan_name = db.Column(db.String(50))
    amount = db.Column(db.Float)
    start_date = db.Column(db.DateTime, default=datetime.datetime.now)
    end_date = db.Column(db.DateTime)
    total_rate = db.Column(db.Float)
    status = db.Column(db.String(20), default='ativo')

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    mp_id = db.Column(db.String(50))
    amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending')

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    minutes = db.Column(db.Integer)
    rate = db.Column(db.Float)

# --- INIT ---
with app.app_context():
    db.create_all()
    if not SystemConfig.query.first(): db.session.add(SystemConfig())
    if not Plan.query.first(): db.session.add(Plan(name="Start", minutes=60, rate=0.02))
    db.session.commit()

# --- HELPERS ---
def get_config(): return SystemConfig.query.first()
def log_action(user, game, action, amt, det):
    db.session.add(GameLog(user=user, game=game, action=action, amount=amt, details=det))
    db.session.commit()

# --- HEALTH CHECK (WAKE UP RENDER) ---
@app.route('/health', methods=['GET'])
def health(): return jsonify({"status": "ok", "time": str(datetime.datetime.now())})

# --- USER & AUTH ---
@app.route('/login', methods=['POST'])
def login():
    d = request.json
    u = User.query.filter((User.username==d['login']) | (User.email==d['login'])).first()
    if u and check_password_hash(u.password, d['password']):
        return jsonify({"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level, "restricted":u.restricted})
    return jsonify({"erro":True, "msg":"Dados inválidos"}), 401

@app.route('/register', methods=['POST'])
def register():
    d = request.json
    if User.query.filter_by(username=d['username']).first(): return jsonify({"erro":True, "msg":"Usuário já existe"}), 400
    u = User(username=d['username'], email=d['email'], cpf=d['cpf'], password=generate_password_hash(d['password']))
    db.session.add(u)
    db.session.commit()
    return jsonify({"success":True})

@app.route('/user/<int:uid>')
def get_user(uid):
    u = User.query.get(uid)
    if not u: return jsonify({"erro":True}), 404
    # Atualiza investimentos
    now = datetime.datetime.now()
    invs = Investment.query.filter_by(user_id=uid, status='ativo').all()
    changed = False
    for i in invs:
        if now >= i.end_date:
            profit = i.amount * (1 + i.total_rate)
            u.balance += profit
            i.status = 'pago'
            log_action(u.username, "Invest", "Win", profit, f"Plano {i.plan_name} finalizado")
            changed = True
    if changed: db.session.commit()
    return jsonify({"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level})

# --- GAMES: MINES PRO (SERVER-SIDE LOGIC) ---
@app.route('/game/mines/start', methods=['POST'])
def mines_start():
    cfg = get_config()
    if not cfg.active_mines: return jsonify({"erro":True, "msg":"Manutenção"}), 400
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet'])
    
    if u.balance < bet: return jsonify({"erro":True, "msg":"Saldo insuficiente"}), 400
    
    # Lógica de criação de bombas
    bombs_count = int(d['mines'])
    all_pos = list(range(25))
    random.shuffle(all_pos)
    bomb_pos = all_pos[:bombs_count]
    
    # Salvar sessão segura no banco
    u.balance -= bet
    sess = MinesSession(user_id=u.id, bet=bet, mines_count=bombs_count, bombs_positions=",".join(map(str, bomb_pos)))
    db.session.add(sess)
    db.session.commit()
    
    return jsonify({"success":True, "game_id":sess.id, "balance":u.balance})

@app.route('/game/mines/reveal', methods=['POST'])
def mines_reveal():
    d = request.json
    sess = MinesSession.query.get(d['game_id'])
    if not sess or not sess.active: return jsonify({"erro":True, "msg":"Jogo inválido"}), 400
    
    cfg = get_config()
    u = User.query.get(sess.user_id)
    pos = int(d['pos'])
    bombs = list(map(int, sess.bombs_positions.split(',')))
    
    # Manipulação: Se usuario restrito ou "Forçar Bomba" ativo
    is_bomb = pos in bombs
    if (cfg.mines_force_bomb or u.restricted) and not is_bomb:
        # Move uma bomba para onde ele clicou para forçar a perda
        is_bomb = True
        # Atualiza o log (opcional, complexo para demo)

    if is_bomb:
        sess.active = False
        db.session.commit()
        log_action(u.username, "Mines", "Loss", sess.bet, f"Bomba em {pos}")
        return jsonify({"status":"bomb", "bombs":bombs}) # Retorna onde estavam todas
    else:
        sess.revealed_count += 1
        db.session.commit()
        # Calcula multiplicador real
        # Formula: C(25, revealed) / C(25-mines, revealed)
        # Simplificado para demo:
        mult = 1.0
        for i in range(sess.revealed_count):
            mult *= (25 - i) / (25 - sess.mines_count - i)
        
        # Aplica House Edge (diminui um pouco o lucro real)
        mult = mult * cfg.mines_house_edge 
        
        return jsonify({"status":"safe", "multiplier": mult})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    d = request.json
    sess = MinesSession.query.get(d['game_id'])
    if not sess or not sess.active: return jsonify({"erro":True}), 400
    
    u = User.query.get(sess.user_id)
    mult = float(d['multiplier'])
    win = sess.bet * mult
    
    u.balance += win
    sess.active = False
    
    log_action(u.username, "Mines", "Win", win, f"Cashout x{mult:.2f}")
    db.session.commit()
    return jsonify({"success":True, "win":win, "balance":u.balance})

# --- GAMES: AVIATOR PRO ---
@app.route('/game/aviator/bet', methods=['POST'])
def aviator_bet():
    cfg = get_config()
    if not cfg.active_aviator: return jsonify({"erro":True, "msg":"Manutenção"}), 400
    
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet'])
    
    if u.balance < bet: return jsonify({"erro":True}), 400
    u.balance -= bet
    db.session.commit()
    
    # Servidor decide AGORA onde vai crashar baseada na sorte e config
    crash_point = 0
    if cfg.aviator_force_crash > 1:
        crash_point = cfg.aviator_force_crash
    elif u.restricted:
        crash_point = 1.05 # Perda quase imediata
    else:
        # Algoritmo padrão de crash (Inverso)
        # Gera valores altos raramente
        r = random.random() # 0.0 a 1.0
        crash_point = 0.99 / (1 - r) # Ex: r=0.5 -> 1.98x. r=0.9 -> 9.9x
        if crash_point > 100: crash_point = 100 # Cap
        if crash_point < 1: crash_point = 1.0
        
    return jsonify({"success":True, "crash_at": crash_point, "start_time": time.time(), "balance":u.balance})

@app.route('/game/aviator/win', methods=['POST'])
def aviator_win():
    d = request.json
    u = User.query.get(d['user_id'])
    win = float(d['amount'])
    mult = float(d['mult'])
    
    # Verificação básica de segurança
    # Em produção, deveria validar se o tempo bate com o crash_point
    u.balance += win
    log_action(u.username, "Aviator", "Win", win, f"Saiu em {mult}x")
    db.session.commit()
    return jsonify({"success":True, "balance":u.balance})

# --- GAMES: DOUBLE (ADAPTADO) ---
@app.route('/game/double/spin', methods=['POST'])
def double_spin():
    cfg = get_config()
    if not cfg.active_double: return jsonify({"erro":True, "msg":"Manutenção"}), 400
    
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['amount'])
    color = d['color']
    
    if u.balance < bet: return jsonify({"erro":True}), 400
    u.balance -= bet
    
    # Sorteio
    # 0 = Branco, 1-7 = Vermelho, 8-14 = Preto
    r = random.randint(0, 14)
    res_color = 'white' if r == 0 else ('red' if r <= 7 else 'black')
    
    win_mult = 0
    if color == res_color:
        win_mult = 14 if res_color == 'white' else 2
        
    win_amount = bet * win_mult
    u.balance += win_amount
    
    if win_amount > 0:
        log_action(u.username, "Double", "Win", win_amount, f"Apostou {color}, Deu {res_color}")
    else:
        log_action(u.username, "Double", "Loss", bet, f"Apostou {color}, Deu {res_color}")
        
    db.session.commit()
    
    # Gera bots falsos para o frontend
    bots = []
    names = ["Pedro", "Ana", "Carlos", "VIP_King", "Sorte99", "RichBoy", "NexusGod"]
    for _ in range(5):
        bots.append({"name": random.choice(names), "amount": random.randint(10, 500), "color": random.choice(['red','black','white'])})
        
    return jsonify({
        "success":True, 
        "result_color": res_color, 
        "roll": r, 
        "win_amount": win_amount, 
        "balance": u.balance,
        "bots": bots
    })

# --- INVESTIMENTOS ---
@app.route('/invest/list')
def plans_list(): return jsonify([{"id":p.id, "name":p.name, "minutes":p.minutes, "rate":p.rate} for p in Plan.query.all()])

@app.route('/invest/create', methods=['POST'])
def invest_create():
    cfg = get_config()
    if not cfg.active_invest: return jsonify({"erro":True}), 400
    d = request.json
    u = User.query.get(d['user_id'])
    p = Plan.query.get(d['plan_id'])
    amt = float(d['amount'])
    
    if u.balance < amt: return jsonify({"erro":True}), 400
    u.balance -= amt
    
    end = datetime.datetime.now() + datetime.timedelta(minutes=p.minutes)
    inv = Investment(user_id=u.id, plan_name=p.name, amount=amt, end_date=end, total_rate=p.rate)
    db.session.add(inv)
    db.session.commit()
    return jsonify({"success":True})

@app.route('/invest/my/<int:uid>')
def my_invest(uid):
    get_user(uid) # Trigger check
    invs = Investment.query.filter_by(user_id=uid).order_by(Investment.id.desc()).all()
    # Retornar segundos restantes para o front fazer a contagem
    res = []
    now = datetime.datetime.now()
    for i in invs:
        remaining = (i.end_date - now).total_seconds()
        res.append({
            "id": i.id, "plan": i.plan_name, "amount": i.amount, 
            "expected": i.amount * (1+i.total_rate), 
            "status": i.status, 
            "seconds_left": max(0, int(remaining))
        })
    return jsonify(res)

# --- ADMIN TOTAL ---
@app.route('/admin/data', methods=['GET'])
def admin_data():
    cfg = get_config()
    users = [{"id":u.id, "username":u.username, "balance":u.balance, "restricted":u.restricted} for u in User.query.all()]
    logs = [{"time": l.timestamp.strftime('%H:%M'), "user":l.user, "game":l.game, "action":l.action, "amt":l.amount, "det":l.details} for l in GameLog.query.order_by(GameLog.id.desc()).limit(50).all()]
    return jsonify({
        "config": {
            "mines_act": cfg.active_mines, "mines_edge": cfg.mines_house_edge, "mines_force": cfg.mines_force_bomb,
            "aviator_act": cfg.active_aviator, "aviator_crash": cfg.aviator_force_crash,
            "double_act": cfg.active_double
        },
        "users": users,
        "logs": logs
    })

@app.route('/admin/update', methods=['POST'])
def admin_up():
    d = request.json
    c = get_config()
    if 'mines_act' in d: c.active_mines = d['mines_act']
    if 'mines_force' in d: c.mines_force_bomb = d['mines_force']
    if 'aviator_crash' in d: c.aviator_force_crash = float(d['aviator_crash'])
    # Adicionar outros campos conforme necessário
    db.session.commit()
    return jsonify({"success":True})

@app.route('/admin/user_action', methods=['POST'])
def admin_u_act():
    d = request.json
    u = User.query.get(d['id'])
    if d['type'] == 'balance_add': u.balance += float(d['val'])
    if d['type'] == 'balance_sub': u.balance -= float(d['val'])
    if d['type'] == 'restrict': u.restricted = d['val']
    db.session.commit()
    return jsonify({"success":True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
