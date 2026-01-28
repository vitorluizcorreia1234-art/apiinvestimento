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

# --- CONFIGURAÇÃO BANCO DE DADOS (ANTI-RESET NO RENDER) ---
# Se houver banco configurado no Render (PostgreSQL), usa ele. Senão, usa arquivo local.
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///nexus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get("SECRET_KEY", "nexus_chave_secreta_suprema")

db = SQLAlchemy(app)
CORS(app)

# --- CONFIG MERCADO PAGO ---
# Coloque seu Token aqui ou nas variáveis de ambiente do Render
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-5404172795263183-120500-011ecc797888559f820986bea6fd264b-511797801")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

ADMIN_PIN = "1234"

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False)      # NOVO
    phone = db.Column(db.String(20), nullable=False)                 # NOVO
    password = db.Column(db.String(255), nullable=False)             # Hash da senha
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')
    reset_token = db.Column(db.String(10), nullable=True)            # Codigo de recuperação

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

# --- INICIALIZAÇÃO ---
with app.app_context():
    db.create_all()
    # Cria dados padrão se não existirem
    if not Plan.query.first():
        db.session.add(Plan(name="Crash 24h", duration_minutes=1440, total_rate=0.05, min_entry=50))
    if not GameConfig.query.first():
        db.session.add(GameConfig())
    if not SystemStatus.query.first():
        db.session.add(SystemStatus())
    db.session.commit()

# --- FUNÇÕES AUXILIARES ---
def check_maintenance(game_type):
    status = SystemStatus.query.first()
    if game_type == 'invest' and not status.active_invest: return True
    if game_type == 'double' and not status.active_double: return True
    return False

def validate_password_strength(password):
    # Minimo 6 chars, pelo menos 1 numero
    if len(password) < 6: return False
    if not re.search(r"\d", password): return False
    return True

# --- ROTAS DE AUTENTICAÇÃO E RECUPERAÇÃO ---

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    cpf = data.get('cpf')
    phone = data.get('phone')

    # Validações Básicas
    if not all([username, email, password, cpf, phone]):
        return jsonify({"erro": True, "msg": "Preencha todos os campos!"}), 400

    if not validate_password_strength(password):
        return jsonify({"erro": True, "msg": "Senha fraca! Use min. 6 caracteres e 1 número."}), 400

    # Verifica duplicidade
    if User.query.filter((User.username == username) | (User.email == email) | (User.cpf == cpf)).first():
        return jsonify({"erro": True, "msg": "Usuário, Email ou CPF já cadastrados!"}), 400

    # Cria Hash da senha (Segurança)
    hashed_pw = generate_password_hash(password)
    
    new_user = User(username=username, email=email, password=hashed_pw, cpf=cpf, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({"success": True, "msg": "Conta criada com sucesso!"})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    # Tenta achar por username, email ou cpf
    user = User.query.filter(
        (User.username == data['login']) | 
        (User.email == data['login']) | 
        (User.cpf == data['login'])
    ).first()

    if user and check_password_hash(user.password, data['password']):
        return jsonify({
            "id": user.id, 
            "username": user.username, 
            "balance": user.balance, 
            "vip_level": user.vip_level
        })
    
    return jsonify({"erro": True, "msg": "Dados incorretos"}), 401

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    data = request.json
    email = data.get('email')
    user = User.query.filter_by(email=email).first()
    
    if not user:
        # Por segurança, não dizemos se o email existe ou não, apenas damos msg genérica
        return jsonify({"success": True, "msg": "Se o email existir, enviamos um código."})

    # Gera código de 6 dígitos
    code = str(random.randint(100000, 999999))
    user.reset_token = code
    db.session.commit()

    # --- SIMULAÇÃO DE ENVIO DE EMAIL ---
    # Aqui entraria a biblioteca SMTP para enviar o email real.
    # Como não temos SMTP configurado, vou printar no console do Render.
    print(f"========================================")
    print(f"RECUPERAÇÃO DE SENHA PARA: {email}")
    print(f"CÓDIGO: {code}")
    print(f"========================================")

    # Retorno o código no JSON APENAS para facilitar seus testes agora. 
    # Em produção real, remova o campo "debug_code".
    return jsonify({"success": True, "msg": "Código enviado (Verifique Console)", "debug_code": code})

@app.route('/reset_password', methods=['POST'])
def reset_password():
    data = request.json
    email = data.get('email')
    code = data.get('code')
    new_pass = data.get('new_password')

    if not validate_password_strength(new_pass):
        return jsonify({"erro": True, "msg": "Senha fraca!"}), 400

    user = User.query.filter_by(email=email).first()
    
    if user and user.reset_token == code:
        user.password = generate_password_hash(new_pass)
        user.reset_token = None # Limpa o token usado
        db.session.commit()
        return jsonify({"success": True, "msg": "Senha alterada com sucesso!"})
    
    return jsonify({"erro": True, "msg": "Código inválido ou expirado."}), 400

# --- PAGAMENTO (MERCADO PAGO) ---
@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    try:
        data = request.json
        user_id = data.get('user_id')
        amount = float(data.get('amount'))
        user = User.query.get(user_id)

        if not user: return jsonify({"erro": True, "msg": "Usuário erro"}), 404
        if amount < 1: return jsonify({"erro": True, "msg": "Mínimo R$ 1,00"}), 400

        # Cria pagamento no MP
        payment_data = {
            "transaction_amount": amount,
            "description": f"Recarga Nexus - {user.username}",
            "payment_method_id": "pix",
            "payer": {
                "email": user.email,
                "first_name": user.username,
                "last_name": "User",
                "identification": {
                    "type": "CPF",
                    "number": user.cpf.replace(".", "").replace("-", "") # Remove formatação do CPF
                }
            }
        }

        result = sdk.payment().create(payment_data)
        
        if result["status"] not in [200, 201]:
            print("Erro MP:", result)
            return jsonify({"erro": True, "msg": "Erro ao gerar PIX"}), 500

        response = result["response"]
        mp_id = str(response["id"])
        
        # Salva no banco para monitorar
        pay = Payment(user_id=user.id, mp_id=mp_id, amount=amount)
        db.session.add(pay)
        db.session.commit()

        return jsonify({
            "success": True,
            "payment_id": mp_id,
            "qr_code": response["point_of_interaction"]["transaction_data"]["qr_code"],
            "qr_base64": response["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        })

    except Exception as e:
        print("Erro Exception:", str(e))
        return jsonify({"erro": True, "msg": "Erro interno"}), 500

@app.route('/api/payment/check/<payment_id>', methods=['GET'])
def check_payment(payment_id):
    try:
        pay_record = Payment.query.filter_by(mp_id=payment_id).first()
        if not pay_record: return jsonify({"status": "not_found"})
        
        if pay_record.status == 'approved':
             return jsonify({"status": "approved"})

        # Consulta API Mercado Pago
        mp_res = sdk.payment().get(payment_id)
        mp_status = mp_res["response"]["status"]

        if mp_status == "approved" and pay_record.status != "approved":
            pay_record.status = "approved"
            user = User.query.get(pay_record.user_id)
            user.balance += pay_record.amount
            db.session.commit()
            return jsonify({"status": "approved", "new_balance": user.balance})
        
        return jsonify({"status": mp_status})
    except:
        return jsonify({"status": "error"})


# --- ROTAS EXISTENTES (MANTER LÓGICA) ---
# ... (INVESTIR, MEUS_INVESTIMENTOS, SAQUE, GAME ...)
# Essas rotas abaixo permanecem as mesmas, só adicionei aqui para garantir que o código esteja completo

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"error": "User not found"}), 404
    verificar_investimentos(user_id)
    return jsonify({"id": u.id, "balance": u.balance, "vip_level": u.vip_level, "username": u.username})

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
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Em manutenção!"})
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
    return jsonify([{
        "id": i.id, "plan": i.plan_name, "amount": i.amount,
        "final_return": i.final_return,
        "start_ts": i.start_date.timestamp() * 1000,
        "end_ts": i.end_date.timestamp() * 1000,
        "status": i.status
    } for i in invs])

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
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Em manutenção!"})
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
        if result_color == 'black': win_amount = bet_amount * cfg.mult_black
        elif result_color == 'red': win_amount = bet_amount * cfg.mult_red
        else: win_amount = bet_amount * cfg.mult_white
        user.balance += win_amount
    
    db.session.commit()
    return jsonify({"success": True, "result_color": result_color, "win": is_win, "win_amount": win_amount, "new_balance": user.balance})

# --- ADMIN ROUTES (Mantidas) ---
@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    return jsonify({"success": request.json.get('pin') == ADMIN_PIN})

@app.route('/admin/data', methods=['GET'])
def admin_data():
    return jsonify({
        "users": [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level, "cpf": u.cpf} for u in User.query.all()],
        "plans": [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()],
        "withdrawals": [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status} for w in Withdrawal.query.filter_by(status='pendente').all()],
        "game": {"c_black": GameConfig.query.first().chance_black, "c_red": GameConfig.query.first().chance_red, "c_white": GameConfig.query.first().chance_white, "m_black": GameConfig.query.first().mult_black, "m_red": GameConfig.query.first().mult_red, "m_white": GameConfig.query.first().mult_white},
        "system": {"active_invest": SystemStatus.query.first().active_invest, "active_double": SystemStatus.query.first().active_double}
    })
# (Incluir aqui as outras rotas admin: toggle_system, save_plan, delete_plan, user_action, withdrawal_action, save_game_config iguais ao anterior)
@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    d = request.json; s = SystemStatus.query.first()
    if d['type'] == 'invest': s.active_invest = d['val']
    if d['type'] == 'double': s.active_double = d['val']
    db.session.commit(); return jsonify({"success": True})

@app.route('/admin/save_plan', methods=['POST'])
def save_plan():
    d = request.json; 
    p = Plan.query.get(d['id']) if 'id' in d and d['id'] else Plan()
    if not p.id: db.session.add(p)
    p.name = d['name']; p.duration_minutes = int(d['minutes']); p.total_rate = float(d['rate']); p.min_entry = float(d['min'])
    db.session.commit(); return jsonify({"success": True})

@app.route('/admin/delete_plan/<int:id>', methods=['DELETE'])
def delete_plan(id):
    Plan.query.filter_by(id=id).delete(); db.session.commit(); return jsonify({"success": True})

@app.route('/admin/user_action', methods=['POST'])
def user_action():
    d = request.json; u = User.query.get(d['id'])
    if 'vip' in d: u.vip_level = d['vip']
    if 'balance' in d: u.balance += float(d['balance'])
    db.session.commit(); return jsonify({"success": True})

@app.route('/admin/withdrawal_action', methods=['POST'])
def withdrawal_action():
    d = request.json; w = Withdrawal.query.get(d['id'])
    if w.status == 'pendente':
        if d['action'] == 'approve': w.status = 'aprovado'
        elif d['action'] == 'reject': w.status = 'rejeitado'; User.query.get(w.user_id).balance += w.amount
        db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_game_config', methods=['POST'])
def save_game_config():
    d = request.json; c = GameConfig.query.first()
    c.chance_black = float(d['c_black']); c.chance_red = float(d['c_red']); c.chance_white = float(d['c_white'])
    c.mult_black = float(d['m_black']); c.mult_red = float(d['m_red']); c.mult_white = float(d['m_white'])
    db.session.commit(); return jsonify({"success": True})

@app.route('/system/status', methods=['GET'])
def get_system_status():
    s = SystemStatus.query.first()
    return jsonify({"invest": s.active_invest, "double": s.active_double})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
