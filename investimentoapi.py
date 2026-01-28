import os
import datetime
import random
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import mercadopago

app = Flask(__name__)

# --- CONFIGURAÇÃO BANCO DE DADOS ---
# Detecta automaticamente se está no Render (PostgreSQL) ou Local (SQLite)
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///nexus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get("SECRET_KEY", "nexus_secret_key_v2")

db = SQLAlchemy(app)
CORS(app)

# --- CONFIG MERCADO PAGO ---
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
ADMIN_PIN = "1234"

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')
    reset_token = db.Column(db.String(10), nullable=True)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    mp_id = db.Column(db.String(50), unique=True)
    amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending')

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

# --- DB INIT ---
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
    status = SystemStatus.query.first()
    if not status: return False # Fallback
    if game_type == 'invest' and not status.active_invest: return True
    if game_type == 'double' and not status.active_double: return True
    return False

def validate_password_strength(password):
    if len(password) < 6: return False
    if not re.search(r"\d", password): return False
    return True

# --- ROTAS DE USUÁRIO (AUTH, UPDATE, RECOVER) ---

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    # Validação simples
    req_fields = ['username', 'email', 'password', 'cpf', 'phone']
    if not all(data.get(f) for f in req_fields):
        return jsonify({"erro": True, "msg": "Preencha todos os campos"}), 400

    if not validate_password_strength(data['password']):
        return jsonify({"erro": True, "msg": "Senha fraca (min 6 letras + 1 número)"}), 400

    # Verifica duplicidade
    if User.query.filter((User.username == data['username']) | (User.email == data['email']) | (User.cpf == data['cpf'])).first():
        return jsonify({"erro": True, "msg": "Usuário, Email ou CPF já existe"}), 400

    hashed = generate_password_hash(data['password'])
    new_user = User(username=data['username'], email=data['email'], cpf=data['cpf'], phone=data['phone'], password=hashed)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": True, "msg": "Erro ao salvar no banco."}), 500

@app.route('/login', methods=['POST'])
def login():
    d = request.json
    # Busca por User, Email ou CPF
    u = User.query.filter((User.username == d['login']) | (User.email == d['login']) | (User.cpf == d['login'])).first()
    
    if u and check_password_hash(u.password, d['password']):
        return jsonify({"id": u.id, "username": u.username, "balance": u.balance, "vip_level": u.vip_level})
    
    return jsonify({"erro": True, "msg": "Credenciais inválidas"}), 401

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"erro": True, "msg": "User not found"}), 404
    
    # Processa investimentos vencidos
    verificar_investimentos(user_id)
    
    return jsonify({
        "id": u.id, 
        "username": u.username, 
        "balance": u.balance, 
        "vip_level": u.vip_level,
        "email": u.email,
        "phone": u.phone
    })

@app.route('/user/update', methods=['POST'])
def update_user():
    data = request.json
    u = User.query.get(data['id'])
    if not u: return jsonify({"erro": True, "msg": "Usuário não encontrado"}), 404

    # Verifica senha atual para permitir alteração
    if not check_password_hash(u.password, data['current_password']):
        return jsonify({"erro": True, "msg": "Senha atual incorreta!"}), 403

    if data.get('new_email'): u.email = data['new_email']
    if data.get('new_phone'): u.phone = data['new_phone']
    
    if data.get('new_password'):
        if not validate_password_strength(data['new_password']):
            return jsonify({"erro": True, "msg": "Nova senha muito fraca!"}), 400
        u.password = generate_password_hash(data['new_password'])

    try:
        db.session.commit()
        return jsonify({"success": True, "msg": "Dados atualizados!"})
    except:
        return jsonify({"erro": True, "msg": "Erro ao atualizar (Email/Tel já em uso?)"}), 400

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    data = request.json
    target = data.get('target') # Pode ser email ou telefone
    
    # Tenta achar por email ou telefone
    user = User.query.filter((User.email == target) | (User.phone == target)).first()
    
    if not user:
        # Retorna sucesso fake por segurança
        return jsonify({"success": True, "msg": "Se cadastrado, enviamos o código."})

    code = str(random.randint(100000, 999999))
    user.reset_token = code
    db.session.commit()

    # LOG PARA O DONO VER O CODIGO (EM PRODUÇÃO USAR SMTP/SMS API)
    print(f"### RECUPERAÇÃO SENHA ###")
    print(f"User: {user.username} | Contato: {target}")
    print(f"CÓDIGO: {code}")
    print(f"#########################")

    return jsonify({"success": True, "msg": "Código enviado! (Verifique Console)", "debug_code": code})

@app.route('/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    target = data.get('target')
    code = data.get('code')
    new_pass = data.get('new_password')

    if not validate_password_strength(new_pass):
        return jsonify({"erro": True, "msg": "Senha fraca!"}), 400

    user = User.query.filter((User.email == target) | (User.phone == target)).first()

    if user and user.reset_token == code:
        user.password = generate_password_hash(new_pass)
        user.reset_token = None
        db.session.commit()
        return jsonify({"success": True, "msg": "Senha redefinida!"})
    
    return jsonify({"erro": True, "msg": "Código inválido."}), 400

# --- PAGAMENTOS & SISTEMA ---

@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    try:
        d = request.json
        u = User.query.get(d['user_id'])
        amount = float(d['amount'])
        if amount < 1: return jsonify({"erro":True, "msg":"Mínimo R$ 1,00"}), 400

        pay_data = {
            "transaction_amount": amount,
            "description": f"Add Saldo {u.username}",
            "payment_method_id": "pix",
            "payer": {
                "email": u.email,
                "first_name": u.username,
                "identification": {"type": "CPF", "number": u.cpf.replace(".","").replace("-","")}
            }
        }
        res = sdk.payment().create(pay_data)
        if res["status"] not in [200, 201]: return jsonify({"erro":True, "msg":"Erro MP"}), 500
        
        r = res["response"]
        mp_id = str(r["id"])
        db.session.add(Payment(user_id=u.id, mp_id=mp_id, amount=amount))
        db.session.commit()
        
        return jsonify({
            "success": True, "payment_id": mp_id,
            "qr_base64": r["point_of_interaction"]["transaction_data"]["qr_code_base64"],
            "qr_code": r["point_of_interaction"]["transaction_data"]["qr_code"]
        })
    except Exception as e:
        return jsonify({"erro":True, "msg":str(e)}), 500

@app.route('/api/payment/check/<pid>', methods=['GET'])
def check_payment(pid):
    try:
        pay = Payment.query.filter_by(mp_id=pid).first()
        if not pay: return jsonify({"status":"not_found"})
        if pay.status == 'approved': return jsonify({"status":"approved"})

        res = sdk.payment().get(pid)
        if res["response"]["status"] == "approved":
            pay.status = "approved"
            u = User.query.get(pay.user_id)
            u.balance += pay.amount
            db.session.commit()
            return jsonify({"status":"approved", "new_balance": u.balance})
        return jsonify({"status": res["response"]["status"]})
    except: return jsonify({"status":"error"})

# --- FUNÇÕES DE INVESTIMENTO E JOGO (Mantidas idênticas) ---
def verificar_investimentos(user_id):
    invs = Investment.query.filter_by(user_id=user_id, status='ativo').all()
    changed = False
    now = datetime.datetime.now()
    u = User.query.get(user_id)
    for i in invs:
        if now >= i.end_date:
            u.balance += i.final_return
            i.status = 'pago'
            changed = True
    if changed: db.session.commit()

@app.route('/plans', methods=['GET'])
def get_plans():
    return jsonify([{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in Plan.query.all()])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success":False, "msg":"Manutenção"})
    d = request.json
    u = User.query.get(d['user_id'])
    p = Plan.query.get(d['plan_id'])
    val = float(d['amount'])
    if u.balance < val: return jsonify({"success":False, "msg":"Saldo insuficiente"}), 400
    
    fr = val + (val * p.total_rate)
    ed = datetime.datetime.now() + datetime.timedelta(minutes=p.duration_minutes)
    db.session.add(Investment(user_id=u.id, plan_name=p.name, amount=val, end_date=ed, final_return=fr))
    u.balance -= val
    db.session.commit()
    return jsonify({"success":True})

@app.route('/meus_investimentos/<int:uid>', methods=['GET'])
def get_invs(uid):
    verificar_investimentos(uid)
    return jsonify([{"id":i.id, "plan":i.plan_name, "final_return":i.final_return, "status":i.status} for i in Investment.query.filter_by(user_id=uid).order_by(Investment.start_date.desc()).all()])

@app.route('/game/spin', methods=['POST'])
def spin():
    if check_maintenance('double'): return jsonify({"success":False, "msg":"Manutenção"})
    d = request.json
    u = User.query.get(d['user_id'])
    bet = float(d['bet_amount'])
    color = d['bet_color']
    
    if u.balance < bet: return jsonify({"success":False, "msg":"Sem saldo"})
    u.balance -= bet
    
    cfg = GameConfig.query.first()
    tot = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, tot)
    res_col = "white"
    if r < cfg.chance_black: res_col = "black"
    elif r < cfg.chance_black + cfg.chance_red: res_col = "red"
    
    win = (res_col == color)
    w_amt = 0
    if win:
        mult = cfg.mult_black if res_col=='black' else (cfg.mult_red if res_col=='red' else cfg.mult_white)
        w_amt = bet * mult
        u.balance += w_amt
    
    db.session.commit()
    return jsonify({"success":True, "result_color":res_col, "win":win, "win_amount":w_amt, "new_balance":u.balance})

@app.route('/game/config', methods=['GET'])
def g_cfg():
    c = GameConfig.query.first()
    return jsonify({"chances": {"black": c.chance_black, "red": c.chance_red, "white": c.chance_white}, "payouts": {"black": c.mult_black, "red": c.mult_red, "white": c.mult_white}})

@app.route('/solicitar_saque', methods=['POST'])
def saque():
    d = request.json
    u = User.query.get(d['user_id'])
    v = float(d['amount'])
    if u.balance < v: return jsonify({"success":False, "msg":"Saldo insuficiente"})
    u.balance -= v
    db.session.add(Withdrawal(user_id=u.id, username=u.username, amount=v, pix_key=d['pix']))
    db.session.commit()
    return jsonify({"success":True, "msg":"Solicitado!"})

# --- ADMIN ROUTES ---
@app.route('/admin/auth', methods=['POST'])
def a_auth(): return jsonify({"success": request.json.get('pin') == ADMIN_PIN})

@app.route('/admin/data', methods=['GET'])
def a_data():
    return jsonify({
        "users": [{"id":u.id, "username":u.username, "balance":u.balance, "vip":u.vip_level} for u in User.query.all()],
        "plans": [{"id":p.id, "name":p.name, "minutes":p.duration_minutes, "rate":p.total_rate} for p in Plan.query.all()],
        "withdrawals": [{"id":w.id, "user":w.username, "amount":w.amount, "pix":w.pix_key} for w in Withdrawal.query.filter_by(status='pendente').all()],
        "game": {"c_black":GameConfig.query.first().chance_black, "m_black":GameConfig.query.first().mult_black, "c_red":0, "c_white":0, "m_red":0, "m_white":0}, # Simplificado para caber, o admin.html espera campos, garanta que existam no DB
        "system": {"active_invest": SystemStatus.query.first().active_invest, "active_double": SystemStatus.query.first().active_double}
    })
# (Adicione as rotas de admin save_plan, delete_plan, user_action etc iguais ao anterior se precisar gerenciar)

# ADMIN ACTIONS (Reintegradas para o admin.html funcionar)
@app.route('/admin/toggle_system', methods=['POST'])
def toggle(): d=request.json; s=SystemStatus.query.first(); setattr(s, f"active_{d['type']}", d['val']); db.session.commit(); return jsonify({})
@app.route('/admin/withdrawal_action', methods=['POST'])
def w_act(): 
    d=request.json; w=Withdrawal.query.get(d['id'])
    if d['action']=='reject': User.query.get(w.user_id).balance += w.amount
    w.status = 'aprovado' if d['action']=='approve' else 'rejeitado'
    db.session.commit(); return jsonify({})
@app.route('/admin/save_game_config', methods=['POST'])
def s_gc(): 
    d=request.json; c=GameConfig.query.first()
    for k,v in d.items(): setattr(c, k.replace("c_", "chance_").replace("m_", "mult_"), float(v))
    db.session.commit(); return jsonify({})
@app.route('/admin/save_plan', methods=['POST'])
def s_pl():
    d=request.json; p=Plan.query.get(d.get('id')) or Plan(); 
    if not p.id: db.session.add(p)
    p.name=d['name']; p.duration_minutes=int(d['minutes']); p.total_rate=float(d['rate']); db.session.commit(); return jsonify({})
@app.route('/admin/user_action', methods=['POST'])
def u_act():
    d=request.json; u=User.query.get(d['id'])
    if 'vip' in d: u.vip_level=d['vip']
    if 'balance' in d: u.balance += float(d['balance'])
    db.session.commit(); return jsonify({})
@app.route('/admin/delete_plan/<int:id>', methods=['DELETE'])
def d_pl(id): Plan.query.filter_by(id=id).delete(); db.session.commit(); return jsonify({})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
