# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
import os
import random
import re
import mercadopago

# --- CONFIGURAÇÃO INICIAL ---
app = Flask(__name__)

# Configuração de Banco de Dados (Detecta PostgreSQL do Render ou usa SQLite local)
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///nexus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get("SECRET_KEY", "nexus_super_secret_key")

db = SQLAlchemy(app)
CORS(app)

# Configuração Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

ADMIN_PIN = "1234"

# --- MODELOS DO BANCO ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False) # Hash
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    mp_id = db.Column(db.String(50), unique=True)
    amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending') # pending, approved
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

class SystemStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active_invest = db.Column(db.Boolean, default=True)
    active_double = db.Column(db.Boolean, default=True)

# --- CRIAÇÃO DO BANCO ---
with app.app_context():
    db.create_all()
    if not Plan.query.first():
        db.session.add(Plan(name="Crash 24h", duration_minutes=1440, total_rate=0.05, min_entry=50))
    if not GameConfig.query.first():
        db.session.add(GameConfig())
    if not SystemStatus.query.first():
        db.session.add(SystemStatus())
    db.session.commit()

# --- VALIDADOES ---
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def check_maintenance(game_type):
    status = SystemStatus.query.first()
    if game_type == 'invest' and not status.active_invest: return True
    if game_type == 'double' and not status.active_double: return True
    return False

# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if not data.get('username') or not data.get('password') or not data.get('email'):
        return jsonify({"erro": True, "msg": "Preencha todos os campos"}), 400
    
    if not is_valid_email(data['email']):
        return jsonify({"erro": True, "msg": "E-mail inválido"}), 400

    if User.query.filter((User.username == data['username']) | (User.email == data['email'])).first():
        return jsonify({"erro": True, "msg": "Usuário ou E-mail já existe"}), 400
    
    # Hash da senha para segurança
    hashed_pw = generate_password_hash(data['password'])
    new_user = User(username=data['username'], email=data['email'], password=hashed_pw)
    
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['login']).first()
    
    # Verifica Hash
    if user and check_password_hash(user.password, data['password']):
        return jsonify({"id": user.id, "username": user.username, "balance": user.balance, "vip_level": user.vip_level})
    
    return jsonify({"erro": True, "msg": "Dados incorretos"}), 401

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"error": "User not found"}), 404
    verificar_investimentos(user_id)
    return jsonify({"id": u.id, "balance": u.balance, "vip_level": u.vip_level, "username": u.username})

# --- SISTEMA DE PAGAMENTO (MERCADO PAGO) ---
@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    try:
        data = request.json
        user_id = data.get('user_id')
        amount = float(data.get('amount'))
        user = User.query.get(user_id)

        if not user: return jsonify({"erro": True, "msg": "Usuário não encontrado"}), 404
        if amount < 1: return jsonify({"erro": True, "msg": "Mínimo R$ 1,00"}), 400

        payment_data = {
            "transaction_amount": amount,
            "description": f"Recarga Nexus - {user.username}",
            "payment_method_id": "pix",
            "payer": {
                "email": user.email,
                "first_name": user.username,
                "last_name": "User"
            }
        }

        result = sdk.payment().create(payment_data)
        if result["status"] not in [200, 201]:
            return jsonify({"erro": True, "msg": "Erro no MercadoPago"}), 500
        
        response = result["response"]
        mp_id = str(response["id"])
        
        # Salva intenção de pagamento no banco
        new_pay = Payment(user_id=user.id, mp_id=mp_id, amount=amount)
        db.session.add(new_pay)
        db.session.commit()

        return jsonify({
            "success": True,
            "payment_id": mp_id,
            "qr_code": response["point_of_interaction"]["transaction_data"]["qr_code"],
            "qr_base64": response["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        })

    except Exception as e:
        return jsonify({"erro": True, "msg": str(e)}), 500

@app.route('/api/payment/check/<payment_id>', methods=['GET'])
def check_payment(payment_id):
    # Verifica status no MercadoPago
    try:
        # Busca no nosso banco
        pay_record = Payment.query.filter_by(mp_id=payment_id).first()
        if not pay_record:
             return jsonify({"status": "not_found"})
        
        if pay_record.status == 'approved':
             return jsonify({"status": "approved"})

        # Consulta API MP
        mp_res = sdk.payment().get(payment_id)
        mp_status = mp_res["response"]["status"]

        if mp_status == "approved" and pay_record.status != "approved":
            # Atualiza banco e saldo
            pay_record.status = "approved"
            user = User.query.get(pay_record.user_id)
            user.balance += pay_record.amount
            db.session.commit()
            return jsonify({"status": "approved", "new_balance": user.balance})
        
        return jsonify({"status": mp_status})

    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})


# --- OUTRAS ROTAS (INVESTIMENTO, DOUBLE, ADMIN) ---
# (Mantive a lógica original, só adaptando para PostgreSQL e correções menores)

def verificar_investimentos(user_id):
    invs = Investment.query.filter_by(user_id=user_id, status='ativo').all()
    now = datetime.datetime.now()
    u = User.query.get(user_id)
    changed = False
    for i in invs:
        if now >= i.end_date:
            u.balance += i.final_return
            i.status = 'pago'
            changed = True
    if changed: db.session.commit()

@app.route('/plans', methods=['GET'])
def get_plans():
    plans = Plan.query.all()
    return jsonify([{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in plans])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    plan = Plan.query.get(data['plan_id'])
    amount = float(data['amount'])
    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente"}), 400
    final_return = amount + (amount * plan.total_rate)
    end_date = datetime.datetime.now() + datetime.timedelta(minutes=plan.duration_minutes)
    inv = Investment(user_id=user.id, plan_name=plan.name, amount=amount, end_date=end_date, final_return=final_return)
    user.balance -= amount
    db.session.add(inv)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def meus_investimentos(user_id):
    verificar_investimentos(user_id)
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.start_date.desc()).all()
    return jsonify([{"id": i.id, "plan": i.plan_name, "amount": i.amount, "final_return": i.final_return, "start_ts": i.start_date.timestamp() * 1000, "end_ts": i.end_date.timestamp() * 1000, "status": i.status} for i in invs])

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
    return jsonify({"success": True, "msg": "Solicitação enviada!"})

@app.route('/game/config', methods=['GET'])
def get_game_config():
    cfg = GameConfig.query.first()
    return jsonify({"chances": {"black": cfg.chance_black, "red": cfg.chance_red, "white": cfg.chance_white}, "payouts": {"black": cfg.mult_black, "red": cfg.mult_red, "white": cfg.mult_white}})

@app.route('/game/spin', methods=['POST'])
def spin_game():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Manutenção!"})
    data = request.json
    user = User.query.get(data['user_id'])
    bet_amount = float(data['bet_amount'])
    bet_color = data['bet_color']
    if user.balance < bet_amount: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    user.balance -= bet_amount
    cfg = GameConfig.query.first()
    total = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, total)
    result_color = "white"
    if r < cfg.chance_black: result_color = "black"
    elif r < cfg.chance_black + cfg.chance_red: result_color = "red"
    
    is_win = False
    win_amount = 0
    if result_color == bet_color:
        is_win = True
        mult = cfg.mult_black if result_color == 'black' else (cfg.mult_red if result_color == 'red' else cfg.mult_white)
        win_amount = bet_amount * mult
        user.balance += win_amount
    db.session.commit()
    return jsonify({"success": True, "result_color": result_color, "win": is_win, "win_amount": win_amount, "new_balance": user.balance})

# --- ADMIN ROUTES ---
@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    return jsonify({"success": request.json.get('pin') == ADMIN_PIN})

@app.route('/admin/data', methods=['GET'])
def admin_data():
    return jsonify({
        "users": [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level} for u in User.query.all()],
        "plans": [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()],
        "withdrawals": [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status} for w in Withdrawal.query.filter_by(status='pendente').all()],
        "game": {"c_black": GameConfig.query.first().chance_black, "c_red": GameConfig.query.first().chance_red, "c_white": GameConfig.query.first().chance_white, "m_black": GameConfig.query.first().mult_black, "m_red": GameConfig.query.first().mult_red, "m_white": GameConfig.query.first().mult_white},
        "system": {"active_invest": SystemStatus.query.first().active_invest, "active_double": SystemStatus.query.first().active_double}
    })

# (Rotas de admin simplificadas para caber)
@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    d = request.json; s = SystemStatus.query.first()
    if d['type'] == 'invest': s.active_invest = d['val']
    if d['type'] == 'double': s.active_double = d['val']
    db.session.commit(); return jsonify({"success": True})

@app.route('/admin/withdrawal_action', methods=['POST'])
def withdrawal_action():
    d = request.json; w = Withdrawal.query.get(d['id'])
    if w.status == 'pendente':
        if d['action'] == 'approve': w.status = 'aprovado'
        elif d['action'] == 'reject': 
            w.status = 'rejeitado'; User.query.get(w.user_id).balance += w.amount
        db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_game_config', methods=['POST'])
def save_game_config():
    d = request.json; c = GameConfig.query.first()
    c.chance_black = float(d['c_black']); c.chance_red = float(d['c_red']); c.chance_white = float(d['c_white'])
    c.mult_black = float(d['m_black']); c.mult_red = float(d['m_red']); c.mult_white = float(d['m_white'])
    db.session.commit(); return jsonify({"success": True})

@app.route('/admin/user_action', methods=['POST'])
def user_action():
    d = request.json; u = User.query.get(d['id'])
    if 'vip' in d: u.vip_level = d['vip']
    if 'balance' in d: u.balance += float(d['balance'])
    db.session.commit(); return jsonify({"success": True})

@app.route('/system/status', methods=['GET'])
def get_system_status():
    s = SystemStatus.query.first()
    return jsonify({"invest": s.active_invest, "double": s.active_double})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
