from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
import random
import string

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
app.secret_key = "nexus_ultra_secure_key_v4_pro"

db = SQLAlchemy(app)
CORS(app)

ADMIN_PIN = "1234"

# --- CONTROLE DE SESSÃO DE JOGO (Anti-Trapaça Simples) ---
# Armazena o estado atual do jogador: {user_id: {'game': 'aviator', 'bet': 10.0, 'data': ...}}
ACTIVE_SESSIONS = {}

# --- MODELOS (TABELAS) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    # Campos novos para o cadastro completo
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
    type = db.Column(db.String(50)) # entrada, saida
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

# --- INICIALIZAÇÃO DO DB ---
with app.app_context():
    db.create_all()
    # Cria dados padrão se não existirem
    if not Plan.query.first():
        db.session.add(Plan(name="Start 24h", duration_minutes=1440, total_rate=0.05, min_entry=20))
        db.session.add(Plan(name="Turbo 48h", duration_minutes=2880, total_rate=0.15, min_entry=50))
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

# --- ROTAS DE AUTH ---

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    login_input = data.get('login')
    password = data.get('password')
    
    # Busca por user ou email
    user = User.query.filter((User.username == login_input) | (User.email == login_input)).first()

    if user and user.password == password:
        return jsonify({
            "id": user.id,
            "username": user.username,
            "balance": user.balance,
            "vip_level": user.vip_level
        })
    return jsonify({"erro": True, "msg": "Usuário ou senha incorretos"}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    
    # Validações
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"success": False, "msg": "Usuário indisponível"}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({"success": False, "msg": "Email já cadastrado"}), 400
    
    # Tratamento CPF/Phone
    cpf_limpo = data.get('cpf', '').replace('.', '').replace('-', '')
    
    new_user = User(
        username=data['username'],
        email=data['email'],
        password=data['password'],
        cpf=cpf_limpo,
        phone=data.get('phone')
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/auth/recover', methods=['POST'])
def recover_password():
    data = request.json
    user = User.query.filter_by(email=data.get('email')).first()
    
    if not user:
        # Retorno falso para segurança
        return jsonify({"success": True, "msg": "Se o email existir, o código foi enviado."})
    
    code = ''.join(random.choices(string.digits, k=6))
    expires = datetime.datetime.now() + datetime.timedelta(minutes=15)
    
    db.session.add(PasswordReset(user_id=user.id, code=code, expires_at=expires))
    db.session.commit()
    
    # Debug: Mostra no log do servidor (console)
    print(f"### CÓDIGO RECUPERAÇÃO ({user.email}): {code} ###")
    
    return jsonify({"success": True, "msg": "Código enviado para o email.", "debug_code": code})

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
    reset = PasswordReset.query.get(data.get('reset_id'))
    
    if not reset or reset.used:
        return jsonify({"success": False, "msg": "Erro na solicitação"})
    
    user = User.query.get(reset.user_id)
    user.password = data['new_password']
    reset.used = True
    db.session.commit()
    
    return jsonify({"success": True})

# --- STATUS SISTEMA ---
@app.route('/system/status', methods=['GET'])
def system_status():
    s = SystemStatus.query.first()
    return jsonify({
        "invest": s.active_invest,
        "double": s.active_double,
        "mines": s.active_mines,
        "aviator": s.active_aviator
    })

# --- USER INFO ---
@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"error": True}), 404
    
    # Atualiza investimentos vencidos
    now = datetime.datetime.now()
    invs = Investment.query.filter_by(user_id=u.id, status='ativo').all()
    for i in invs:
        if now >= i.end_date:
            i.status = 'finalizado'
            db.session.commit()
            
    return jsonify({
        "id": u.id, "username": u.username, "balance": u.balance, 
        "vip_level": u.vip_level, "email": u.email, "phone": u.phone
    })

# --- INVESTIMENTOS ---
@app.route('/plans', methods=['GET'])
def get_plans():
    return jsonify([{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Investimentos em manutenção"})
    data = request.json
    u = User.query.get(data['user_id'])
    p = Plan.query.get(data['plan_id'])
    amt = float(data['amount'])
    
    if u.balance < amt: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    u.balance -= amt
    final = amt + (amt * p.total_rate)
    end = datetime.datetime.now() + datetime.timedelta(minutes=p.duration_minutes)
    
    inv = Investment(user_id=u.id, plan_name=p.name, amount=amt, end_date=end, final_return=final)
    db.session.add(inv)
    registrar_log('entrada', amt, f"Investimento {p.name}")
    db.session.commit()
    return jsonify({"success": True})

@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def my_investments(user_id):
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.id.desc()).all()
    return jsonify([{
        "id": i.id, "plan": i.plan_name, "amount": i.amount, "status": i.status, 
        "final_return": i.final_return
    } for i in invs])

@app.route('/invest/withdraw_profit', methods=['POST'])
def withdraw_profit():
    data = request.json
    inv = Investment.query.get(data['invest_id'])
    
    if not inv or inv.status != 'finalizado':
        return jsonify({"success": False, "msg": "Ainda não disponível"})
    
    u = User.query.get(inv.user_id)
    u.balance += inv.final_return
    inv.status = 'pago'
    registrar_log('saida', inv.final_return, "Lucro Investimento")
    db.session.commit()
    return jsonify({"success": True, "amount": inv.final_return})

@app.route('/solicitar_saque', methods=['POST'])
def solicitar_saque():
    data = request.json
    u = User.query.get(data['user_id'])
    amt = float(data['amount'])
    
    if u.balance < amt: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    u.balance -= amt
    wd = Withdrawal(user_id=u.id, username=u.username, amount=amt, pix_key=data['pix'])
    db.session.add(wd)
    db.session.commit()
    return jsonify({"success": True, "msg": "Saque solicitado!"})

# --- JOGOS (COM SESSÃO SEGURA) ---

@app.route('/game/spin', methods=['POST'])
def spin_double():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Em manutenção"})
    data = request.json
    u = User.query.get(data['user_id'])
    bet = float(data['bet_amount'])
    color = data['bet_color']
    
    if u.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    # Debita
    u.balance -= bet
    registrar_log('entrada', bet, "Double Bet")
    
    # Lógica
    cfg = GameConfig.query.first()
    # Sorteio Simples Ponderado
    roll = random.uniform(0, 100)
    result = "white"
    
    # Ajuste simples baseado na config
    if roll < cfg.chance_black: result = "black"
    elif roll < cfg.chance_black + cfg.chance_red: result = "red"
    
    win = False
    win_amt = 0
    if result == color:
        win = True
        mult = cfg.mult_white if result == 'white' else (2.0)
        win_amt = bet * mult
        u.balance += win_amt
        registrar_log('saida', win_amt, "Double Win")
        
    db.session.commit()
    return jsonify({"success": True, "result_color": result, "win": win, "win_amount": win_amt, "new_balance": u.balance})

# --- AVIATOR SEGURO ---
@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    if check_maintenance('aviator'): return jsonify({"success": False, "msg": "Em manutenção"})
    data = request.json
    user_id = data['user_id']
    bet = float(data['bet_amount'])
    
    u = User.query.get(user_id)
    if u.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    # Inicia Sessão
    u.balance -= bet
    registrar_log('entrada', bet, "Aviator Bet")
    
    cfg = GameConfig.query.first()
    
    # Decide o Crash Point Agora (O Backend decide, o front só exibe)
    if random.uniform(0, 100) < cfg.aviator_edge:
        crash_point = 1.00 # Crash instantâneo (House Edge)
    else:
        # Algoritmo de crash simples
        # 1% chance de crash alto
        x = random.uniform(1, 100)
        crash_point = float("{:.2f}".format(0.99 / (1 - (x/100))))
        if crash_point > cfg.aviator_max_mult: crash_point = cfg.aviator_max_mult
        if crash_point < 1.0: crash_point = 1.0
        
    # Salva na memória que este user está jogando com este crash point
    ACTIVE_SESSIONS[user_id] = {
        'game': 'aviator',
        'bet': bet,
        'crash_point': crash_point,
        'start_time': datetime.datetime.now()
    }
    
    db.session.commit()
    return jsonify({"success": True, "new_balance": u.balance, "crash_point": crash_point})

@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    data = request.json
    user_id = data['user_id']
    win_request = float(data['win_amount']) # O front manda quanto acha que ganhou
    
    # Verifica se tem sessão ativa
    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session['game'] != 'aviator':
        return jsonify({"success": False, "msg": "Nenhuma aposta ativa encontrada."})
    
    # Validação de Segurança
    # O valor que o user pede deve ser menor ou igual ao (bet * crash_point)
    max_possible_win = session['bet'] * session['crash_point']
    
    # Tolerância de arredondamento pequena
    if win_request > (max_possible_win + 0.5): 
        # User tentou roubar ou erro de sync
        del ACTIVE_SESSIONS[user_id]
        return jsonify({"success": False, "msg": "Erro de validação (Crash ocorreu antes)."})
    
    # Paga
    u = User.query.get(user_id)
    u.balance += win_request
    registrar_log('saida', win_request, "Aviator Win")
    
    # Encerra sessão
    del ACTIVE_SESSIONS[user_id]
    db.session.commit()
    
    return jsonify({"success": True, "new_balance": u.balance})


# --- MINES SEGURO ---
@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    if check_maintenance('mines'): return jsonify({"success": False, "msg": "Em manutenção"})
    data = request.json
    user_id = data['user_id']
    bet = float(data['bet_amount'])
    mines_count = int(data.get('mines_count', 3))
    
    u = User.query.get(user_id)
    if u.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    u.balance -= bet
    registrar_log('entrada', bet, "Mines Bet")
    
    cfg = GameConfig.query.first()
    rigged = False
    if random.uniform(0, 100) < cfg.mines_edge: rigged = True
    
    # Inicia Sessão Mines
    ACTIVE_SESSIONS[user_id] = {
        'game': 'mines',
        'bet': bet,
        'mines': mines_count,
        'rigged': rigged
    }
    
    db.session.commit()
    return jsonify({"success": True, "new_balance": u.balance, "rigged": rigged})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user_id = data['user_id']
    win_amt = float(data['win_amount'])
    
    # Verifica sessão
    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session['game'] != 'mines':
        return jsonify({"success": False, "msg": "Sem jogo ativo"})
        
    # Validar limite máximo teórico (opcional, mas bom pra evitar hack absurdo)
    # Aqui confiamos que o jogo Mines só chama cashout se não explodiu
    # Mas como o front controla a explosão baseada no 'rigged' e probabilidade,
    # Aceitamos o cashout se houver sessão.
    
    u = User.query.get(user_id)
    u.balance += win_amt
    registrar_log('saida', win_amt, "Mines Win")
    
    del ACTIVE_SESSIONS[user_id]
    db.session.commit()
    
    return jsonify({"success": True, "new_balance": u.balance})

# --- ADMIN API (Simplificada para manter compatibilidade) ---

@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403

@app.route('/admin/data', methods=['GET'])
def admin_data():
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level, "cpf": u.cpf} for u in User.query.all()]
    plans = [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()]
    withdrawals = [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status, "date": w.date.strftime('%d/%m %H:%M')} for w in Withdrawal.query.filter_by(status='pendente').all()]
    
    sys = SystemStatus.query.first()
    cfg = GameConfig.query.first()
    
    return jsonify({
        "users": users, "plans": plans, "withdrawals": withdrawals,
        "system": {"active_invest": sys.active_invest, "active_double": sys.active_double, "active_mines": sys.active_mines, "active_aviator": sys.active_aviator},
        "game": {"c_black": cfg.chance_black, "c_red": cfg.chance_red, "c_white": cfg.chance_white, "m_black": cfg.mult_black, "m_red": cfg.mult_red, "m_white": cfg.mult_white, "mines_edge": cfg.mines_edge, "aviator_max": cfg.aviator_max_mult, "aviator_edge": cfg.aviator_edge}
    })

@app.route('/admin/toggle_system', methods=['POST'])
def adm_toggle():
    d = request.json
    s = SystemStatus.query.first()
    v = d['val']
    if d['type'] == 'invest': s.active_invest = v
    if d['type'] == 'double': s.active_double = v
    if d['type'] == 'mines': s.active_mines = v
    if d['type'] == 'aviator': s.active_aviator = v
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_game_config', methods=['POST'])
def adm_save_game():
    d = request.json
    c = GameConfig.query.first()
    # Atualiza campos se existirem no json
    for k, v in d.items():
        if hasattr(c, k): setattr(c, k, float(v)) # Cuidado com mapeamento de nomes, idealmente mapear manual
    # Mapeamento manual rápido para garantir nomes do admin.html
    if 'c_black' in d: c.chance_black = float(d['c_black'])
    if 'c_red' in d: c.chance_red = float(d['c_red'])
    if 'c_white' in d: c.chance_white = float(d['c_white'])
    if 'm_black' in d: c.mult_black = float(d['m_black'])
    if 'm_red' in d: c.mult_red = float(d['m_red'])
    if 'm_white' in d: c.mult_white = float(d['m_white'])
    if 'mines_edge' in d: c.mines_edge = float(d['mines_edge'])
    if 'aviator_edge' in d: c.aviator_edge = float(d['aviator_edge'])
    if 'aviator_max' in d: c.aviator_max_mult = float(d['aviator_max'])
    
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_plan', methods=['POST'])
def adm_save_plan():
    d = request.json
    p = Plan(name=d['name'], duration_minutes=int(d['minutes']), total_rate=float(d['rate']), min_entry=float(d['min']))
    db.session.add(p)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/delete_plan/<int:id>', methods=['DELETE'])
def adm_del_plan(id):
    Plan.query.filter_by(id=id).delete()
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/user_action', methods=['POST'])
def adm_user_action():
    d = request.json
    u = User.query.get(d['id'])
    if 'vip' in d: u.vip_level = d['vip']
    if 'balance' in d: u.balance += float(d['balance'])
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/withdrawal_action', methods=['POST'])
def adm_wd_action():
    d = request.json
    w = Withdrawal.query.get(d['id'])
    if d['action'] == 'approve': w.status = 'aprovado'
    elif d['action'] == 'reject':
        w.status = 'rejeitado'
        User.query.get(w.user_id).balance += w.amount
    db.session.commit()
    return jsonify({"success": True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
