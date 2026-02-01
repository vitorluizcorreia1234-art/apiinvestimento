from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
import random
import string
import re
import base64

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "nexus_core_system_v99"

db = SQLAlchemy(app)
CORS(app)

ADMIN_PIN = "1234"

# --- UTILITÁRIOS ---
def clean_input(val):
    """Remove tudo que não for número (para CPF e Telefone)"""
    if not val: return ""
    return re.sub(r'\D', '', str(val))

def generate_pix_payload(amount, user_id):
    """Gera um código PIX Copia e Cola (Simulado mas formato válido para QR)"""
    # Em produção, você usaria uma API (MercadoPago, StarkBank, etc)
    # Aqui geramos uma string única para o frontend gerar o QR
    txid = f"NEXUS{user_id}{int(datetime.datetime.now().timestamp())}"
    return f"00020126330014BR.GOV.BCB.PIX0111yourkey@pix.com520400005303986540{amount:.2f}5802BR5909NEXUS_PAY6009SAO_PAULO62070503{txid}6304"

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=True) 
    phone = db.Column(db.String(20), unique=True, nullable=True)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')

class Deposit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount = db.Column(db.Float, nullable=False)
    payload = db.Column(db.String(500))
    status = db.Column(db.String(20), default='pendente') # pendente, pago
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    code = db.Column(db.String(6))
    expires_at = db.Column(db.DateTime)
    used = db.Column(db.Boolean, default=False)
    method = db.Column(db.String(10)) # email ou sms

class GameConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Double
    c_black = db.Column(db.Float, default=45.0)
    c_red = db.Column(db.Float, default=45.0)
    c_white = db.Column(db.Float, default=10.0)
    m_black = db.Column(db.Float, default=2.0)
    m_red = db.Column(db.Float, default=2.0)
    m_white = db.Column(db.Float, default=14.0)
    # Risk
    mines_edge = db.Column(db.Float, default=30.0)
    aviator_edge = db.Column(db.Float, default=10.0)
    aviator_max = db.Column(db.Float, default=10.0)

class SystemStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active_invest = db.Column(db.Boolean, default=True)
    active_double = db.Column(db.Boolean, default=True)
    active_mines = db.Column(db.Boolean, default=True)
    active_aviator = db.Column(db.Boolean, default=True)

# Investment, Withdrawal, FinancialLog e Plan mantidos simplificados
class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50)); duration_minutes = db.Column(db.Integer); total_rate = db.Column(db.Float); min_entry = db.Column(db.Float)
class Investment(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer); plan_name = db.Column(db.String(50)); amount = db.Column(db.Float); end_date = db.Column(db.DateTime); final_return = db.Column(db.Float); status = db.Column(db.String(20))
class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer); username = db.Column(db.String(80)); amount = db.Column(db.Float); pix_key = db.Column(db.String(100)); status = db.Column(db.String(20)); date = db.Column(db.DateTime, default=datetime.datetime.now)

# --- INIT ---
with app.app_context():
    db.create_all()
    if not GameConfig.query.first(): db.session.add(GameConfig())
    if not SystemStatus.query.first(): db.session.add(SystemStatus())
    db.session.commit()

# --- AUTH AVANÇADO ---
@app.route('/login', methods=['POST'])
def login():
    d = request.json
    login_input = d.get('login', '').strip()
    password = d.get('password')
    
    clean_val = clean_input(login_input) # Apenas números para tentar match em CPF/Tel

    # Busca Hierárquica: Username -> Email -> CPF -> Telefone
    user = User.query.filter(
        (User.username == login_input) |
        (User.email == login_input) |
        (User.cpf == clean_val) |
        (User.phone == clean_val)
    ).first()

    if user and user.password == password:
        return jsonify({
            "success": True, "id": user.id, "username": user.username,
            "balance": user.balance, "vip": user.vip_level
        })
    return jsonify({"success": False, "msg": "Dados de acesso incorretos."}), 401

@app.route('/register', methods=['POST'])
def register():
    d = request.json
    cpf = clean_input(d.get('cpf'))
    phone = clean_input(d.get('phone'))
    
    # Validações Rígidas
    if User.query.filter_by(username=d['username']).first(): return jsonify({"success":False, "msg": "Usuário já existe"}), 400
    if User.query.filter_by(email=d['email']).first(): return jsonify({"success":False, "msg": "Email já usado"}), 400
    if User.query.filter_by(cpf=cpf).first(): return jsonify({"success":False, "msg": "CPF já cadastrado"}), 400
    if User.query.filter_by(phone=phone).first(): return jsonify({"success":False, "msg": "Telefone já cadastrado"}), 400
    
    new_user = User(username=d['username'], email=d['email'], password=d['password'], cpf=cpf, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/auth/recover', methods=['POST'])
def recover():
    d = request.json
    val = d.get('contact') # Pode ser email ou tel
    clean_val = clean_input(val)
    
    user = User.query.filter((User.email == val) | (User.phone == clean_val)).first()
    if not user: return jsonify({"success": True, "msg": "Código enviado (se existir)."}) # Fake success para segurança

    code = ''.join(random.choices(string.digits, k=6))
    expr = datetime.datetime.now() + datetime.timedelta(minutes=10)
    
    # Salva código
    reset = PasswordReset(user_id=user.id, code=code, expires_at=expr, method='generic')
    db.session.add(reset)
    db.session.commit()
    
    # LOG NO CONSOLE PARA TESTE (Em produção usaria SMTP/SMS Gateway)
    print(f"\n[SISTEMA DE RECUPERAÇÃO] Código para {user.username}: {code}\n")
    
    return jsonify({"success": True, "msg": "Código enviado! Verifique SMS/Email.", "dev_code": code})

@app.route('/auth/reset', methods=['POST'])
def reset_pass():
    d = request.json
    reset = PasswordReset.query.filter_by(code=d['code'], used=False).first()
    if not reset or reset.expires_at < datetime.datetime.now():
        return jsonify({"success": False, "msg": "Código inválido ou expirado"})
    
    user = User.query.get(reset.user_id)
    user.password = d['new_password']
    reset.used = True
    db.session.commit()
    return jsonify({"success": True})

# --- SISTEMA PIX (Depósito) ---
@app.route('/deposit/generate', methods=['POST'])
def deposit_generate():
    d = request.json
    user = User.query.get(d['user_id'])
    amount = float(d['amount'])
    
    if amount < 1: return jsonify({"success": False, "msg": "Mínimo R$ 1.00"})

    # Gera payload
    payload = generate_pix_payload(amount, user.id)
    
    dep = Deposit(user_id=user.id, amount=amount, payload=payload)
    db.session.add(dep)
    db.session.commit()
    
    return jsonify({
        "success": True, 
        "payload": payload, 
        "qr_img": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={payload}",
        "deposit_id": dep.id
    })

@app.route('/deposit/check/<int:dep_id>', methods=['GET'])
def check_deposit(dep_id):
    # Endpoint para o front verificar se caiu (Long Polling)
    # No "God Mode", você pode criar um botão no admin para aprovar depósitos manuais se não tiver gateway real
    dep = Deposit.query.get(dep_id)
    return jsonify({"status": dep.status})

# --- ROTAS ADMIN E JOGOS (Compactadas mas com lógica completa) ---
@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    return jsonify({"success": True}) if request.json.get('pin') == ADMIN_PIN else jsonify({"success": False})

@app.route('/admin/data', methods=['GET'])
def admin_data():
    # Retorna dados completos para o Admin Panel
    users = [{"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level} for u in User.query.all()]
    wds = [{"id":w.id, "user":w.username, "amount":w.amount, "pix":w.pix_key, "date":w.date.strftime('%d/%m %H:%M')} for w in Withdrawal.query.filter_by(status='pendente').all()]
    cfg = GameConfig.query.first()
    sys = SystemStatus.query.first()
    return jsonify({
        "users": users, "withdrawals": wds,
        "game": {"c_black": cfg.c_black, "c_red": cfg.c_red, "c_white": cfg.c_white, "m_black": cfg.m_black, "m_red": cfg.m_red, "mines_edge": cfg.mines_edge, "aviator_max": cfg.aviator_max},
        "system": {"active_double": sys.active_double, "active_mines": sys.active_mines, "active_aviator": sys.active_aviator}
    })

# User Info
@app.route('/user/<int:uid>', methods=['GET'])
def get_user(uid):
    u = User.query.get(uid)
    return jsonify({"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level}) if u else jsonify({"error":True}), 404

# Saques
@app.route('/solicitar_saque', methods=['POST'])
def saque():
    d = request.json
    u = User.query.get(d['user_id'])
    amt = float(d['amount'])
    if u.balance < amt: return jsonify({"success":False, "msg":"Saldo Insuficiente"})
    u.balance -= amt
    db.session.add(Withdrawal(user_id=u.id, username=u.username, amount=amt, pix_key=d['pix']))
    db.session.commit()
    return jsonify({"success":True, "msg": "Saque solicitado!"})

# --- LÓGICA DE JOGO (Double, Mines, Aviator) ---
@app.route('/game/spin', methods=['POST']) # Double
def double_spin():
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet_amount']); color = d['bet_color']
    
    if u.balance < bet: return jsonify({"success":False, "msg":"Sem saldo"})
    u.balance -= bet
    
    cfg = GameConfig.query.first()
    # Lógica de Probabilidade
    rng = random.uniform(0, 100)
    res = "white"
    if rng < cfg.c_black: res = "black"
    elif rng < cfg.c_black + cfg.c_red: res = "red"
    
    win = (res == color)
    w_amt = 0
    if win:
        mult = cfg.m_white if res == 'white' else (cfg.m_black if res == 'black' else cfg.m_red)
        w_amt = bet * mult
        u.balance += w_amt
        
    db.session.commit()
    return jsonify({"success":True, "result_color":res, "win":win, "win_amount":w_amt, "new_balance":u.balance})

@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet_amount'])
    if u.balance < bet: return jsonify({"success":False, "msg":"Sem saldo"})
    u.balance -= bet
    
    cfg = GameConfig.query.first()
    # Se "Edge" (Chance da casa) bater, crasha em 1.00x
    crash = 1.00
    if random.uniform(0,100) > cfg.aviator_edge:
        # Gera multiplicador
        mult = 1.0 + random.expovariate(0.15) # Curva exponencial
        if mult > cfg.aviator_max: mult = cfg.aviator_max
        crash = round(mult, 2)
        
    db.session.commit()
    return jsonify({"success":True, "crash_point":crash, "new_balance":u.balance})

@app.route('/game/aviator/cashout', methods=['POST'])
def aviator_cashout():
    d = request.json
    u = User.query.get(d['user_id'])
    u.balance += float(d['win_amount'])
    db.session.commit()
    return jsonify({"success":True, "new_balance":u.balance})

@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet_amount'])
    if u.balance < bet: return jsonify({"success":False, "msg":"Sem saldo"})
    u.balance -= bet
    
    cfg = GameConfig.query.first()
    rigged = (random.uniform(0,100) < cfg.mines_edge)
    db.session.commit()
    return jsonify({"success":True, "new_balance":u.balance, "rigged":rigged})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    # Igual ao aviator, só soma
    d = request.json
    u = User.query.get(d['user_id'])
    u.balance += float(d['win_amount'])
    db.session.commit()
    return jsonify({"success":True, "new_balance":u.balance})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
