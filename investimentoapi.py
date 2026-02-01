from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
import random
import time



app = Flask(__name__)
db_url = os.environ.get("DATABASE_URL")

if not db_url:
    raise RuntimeError("DATABASE_URL não configurada no Render")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "nexus_secret_key"

db = SQLAlchemy(app)
CORS(app)
CORS(app, resources={r"/*": {"origins": "*"}})
ADMIN_PIN = "1234"


# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante')


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
    status = db.Column(db.String(20), default='ativo')  # ativo, pago


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
    if game_type == 'invest' and not status.active_invest:
        return True
    if game_type == 'double' and not status.active_double:
        return True
    return False


# --- ROTAS GERAIS ---
@app.route('/system/status', methods=['GET'])
def get_system_status():
    s = SystemStatus.query.first()
    return jsonify({"invest": s.active_invest, "double": s.active_double})


@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['login']).first()
    if user and user.password == data['password']:
        return jsonify({"id": user.id, "username": user.username, "balance": user.balance, "vip_level": user.vip_level})
    return jsonify({"erro": True, "msg": "Dados incorretos"}), 401


@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"erro": True, "msg": "Usuário já existe"}), 400
    new_user = User(username=data['username'], email=data['email'], password=data['password'])
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})


@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    verificar_investimentos(user_id)
    return jsonify({"id": u.id, "balance": u.balance, "vip_level": u.vip_level, "username": u.username})


# --- SISTEMA DE INVESTIMENTO ---
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
    if changed:
        db.session.commit()


@app.route('/plans', methods=['GET'])
def get_plans():
    plans = Plan.query.all()
    return jsonify(
        [{"id": p.id, "name": p.name, "min": p.min_entry, "minutes": p.duration_minutes, "rate": p.total_rate} for p in
         plans])


@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Investimentos em manutenção!"})

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
    return jsonify({"success": True, "msg": "Investimento realizado!"})


@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def meus_investimentos(user_id):
    verificar_investimentos(user_id)
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.start_date.desc()).all()
    # Retornar timestamps para facilitar o contador no JS
    return jsonify([{
        "id": i.id, "plan": i.plan_name, "amount": i.amount,
        "final_return": i.final_return,
        "start_ts": i.start_date.timestamp() * 1000,
        "end_ts": i.end_date.timestamp() * 1000,
        "status": i.status
    } for i in invs])


# --- SAQUE ---
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
    return jsonify({"success": True, "msg": "Solicitação enviada! Aguarde aprovação."})


# --- DOUBLE ---
@app.route('/game/config', methods=['GET'])
def get_game_config():
    cfg = GameConfig.query.first()
    return jsonify({
        "chances": {"black": cfg.chance_black, "red": cfg.chance_red, "white": cfg.chance_white},
        "payouts": {"black": cfg.mult_black, "red": cfg.mult_red, "white": cfg.mult_white}
    })


@app.route('/game/spin', methods=['POST'])
def spin_game():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Double em manutenção!"})

    data = request.json
    user = User.query.get(data['user_id'])
    bet_amount = float(data['bet_amount'])
    bet_color = data['bet_color']

    if user.balance < bet_amount: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet_amount

    cfg = GameConfig.query.first()
    total_chance = cfg.chance_black + cfg.chance_red + cfg.chance_white
    r = random.uniform(0, total_chance)

    result_color = "white"
    if r < cfg.chance_black:
        result_color = "black"
    elif r < cfg.chance_black + cfg.chance_red:
        result_color = "red"

    win_amount = 0
    is_win = False

    if result_color == bet_color:
        is_win = True
        if result_color == 'black':
            win_amount = bet_amount * cfg.mult_black
        elif result_color == 'red':
            win_amount = bet_amount * cfg.mult_red
        elif result_color == 'white':
            win_amount = bet_amount * cfg.mult_white
        user.balance += win_amount

    db.session.commit()

    return jsonify({
        "success": True, "result_color": result_color,
        "win": is_win, "win_amount": win_amount, "new_balance": user.balance
    })


# --- ADMIN ---
@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403


@app.route('/admin/data', methods=['GET'])
def admin_data():
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level} for u in User.query.all()]
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
                 "m_black": cfg.mult_black, "m_red": cfg.mult_red, "m_white": cfg.mult_white},
        "system": {"active_invest": sys.active_invest, "active_double": sys.active_double}
    })


@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    data = request.json
    s = SystemStatus.query.first()
    if data['type'] == 'invest': s.active_invest = data['val']
    if data['type'] == 'double': s.active_double = data['val']
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


@app.route('/admin/save_game_config', methods=['POST'])
def save_game_config():
    data = request.json
    cfg = GameConfig.query.first()
    cfg.chance_black = float(data['c_black'])
    cfg.chance_red = float(data['c_red'])
    cfg.chance_white = float(data['c_white'])
    cfg.mult_black = float(data['m_black'])
    cfg.mult_red = float(data['m_red'])
    cfg.mult_white = float(data['m_white'])
    db.session.commit()
    return jsonify({"success": True})


if __name__ == '__main__':
    # O Render usa a porta da variável de ambiente PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
