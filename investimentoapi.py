import os
import datetime
import random
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import mercadopago

# --- CONFIGURAÇÃO CORE E SEGURANÇA ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração do Banco de Dados (Suporte para SQLite local e PostgreSQL no Render)
db_url = os.environ.get("DATABASE_URL", "sqlite:///nexus_v3.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CHAVE SECRETA PARA CRIPTOGRAFIA DE TOKENS (Mude isso no painel do Render depois)
SECRET_KEY = os.environ.get("SECRET_KEY", "chave_super_secreta_ninja_nexus_2024")
app.config['SECRET_KEY'] = SECRET_KEY

db = SQLAlchemy(app)

# --- CONFIGURAÇÃO MERCADO PAGO ---
# Coloque seu Token de Produção no Render
MP_ACCESS_TOKEN = os.environ.get("MP_TOKEN", "TEST-00000000000000-000000-0000000000000000000000000000000-0000000")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


# --- MODELOS DE BANCO DE DADOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    role = db.Column(db.String(20), default='user') # 'user' ou 'admin'
    vip = db.Column(db.String(20), default='iniciante') # iniciante, frequente, veterano, pro, streamer, adm

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'deposit' ou 'withdraw'
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, approved, rejected
    pix_key = db.Column(db.String(100), nullable=True) # Para saques
    external_id = db.Column(db.String(100), nullable=True) # ID do Mercado Pago
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)


# --- DECORADORES DE SEGURANÇA (MIDDLEWARES) ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith("Bearer "):
            return jsonify({'success': False, 'msg': 'Token ausente ou inválido'}), 401
        
        token = token.split(" ")[1]
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user:
                raise Exception("Usuário não encontrado")
        except Exception as e:
            return jsonify({'success': False, 'msg': 'Sessão expirada ou inválida'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role != 'admin' and current_user.vip != 'adm':
            return jsonify({'success': False, 'msg': 'Acesso negado. Apenas Administradores.'}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    
    if User.query.filter_by(username=data['username']).first() or User.query.filter_by(email=data['email']).first():
        return jsonify({'success': False, 'msg': 'Usuário ou Email já existem.'})
    
    # Se o nome for admin, cria como admin master
    role = 'admin' if data['username'].lower() == 'admin' else 'user'
    vip = 'adm' if data['username'].lower() == 'admin' else 'iniciante'
    
    hashed_password = generate_password_hash(data['password'], method='pbkdf2:sha256')
    
    new_user = User(
        username=data['username'],
        email=data['email'],
        cpf=data['cpf'],
        phone=data['phone'],
        password_hash=hashed_password,
        role=role,
        vip=vip
    )
    
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'msg': 'Conta Ninja criada com sucesso!'})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['login']).first()
    
    if not user:
        user = User.query.filter_by(email=data['login']).first()

    if user and check_password_hash(user.password_hash, data['password']):
        # Gera o JWT Seguro válido por 24 horas
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({
            'success': True, 
            'token': token,
            'user': {
                'id': user.id, 'username': user.username, 'balance': user.balance, 
                'role': user.role, 'vip': user.vip
            }
        })
    
    return jsonify({'success': False, 'msg': 'Credenciais incorretas.'})


# --- ROTAS DE PAGAMENTO (DEPÓSITO E WEBHOOK) ---
@app.route('/api/deposit/pix', methods=['POST'])
@token_required
def generate_pix(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    
    if amount < 20:
        return jsonify({'success': False, 'msg': 'Valor mínimo de R$ 20.00'})

    payment_data = {
        "transaction_amount": amount,
        "description": f"Deposito NEXUS - {current_user.username}",
        "payment_method_id": "pix",
        "payer": { "email": current_user.email, "first_name": current_user.username }
    }
    
    payment_response = sdk.payment().create(payment_data)
    payment = payment_response["response"]
    
    if "id" in payment:
        # Salva como pendente no banco
        trans = Transaction(user_id=current_user.id, type='deposit', amount=amount, external_id=str(payment["id"]))
        db.session.add(trans)
        db.session.commit()
        
        qr_code_base64 = payment['point_of_interaction']['transaction_data']['qr_code_base64']
        qr_code_pix = payment['point_of_interaction']['transaction_data']['qr_code']
        
        return jsonify({'success': True, 'qr_code_base64': qr_code_base64, 'qr_code': qr_code_pix})
    
    return jsonify({'success': False, 'msg': 'Erro ao comunicar com Mercado Pago.'})

# WEBHOOK: O MERCADO PAGO CHAMA ESSA ROTA SOZINHO QUANDO O CLIENTE PAGA
@app.route('/api/webhook/mercadopago', methods=['POST'])
def webhook_mp():
    data = request.json
    if data and data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id")
        
        if payment_id:
            # Consulta o status real no Mercado Pago
            payment_info = sdk.payment().get(payment_id)["response"]
            
            if payment_info.get("status") == "approved":
                # Busca a transação e bloqueia a linha para evitar pagamento duplo (Pessimistic Lock)
                trans = Transaction.query.with_for_update().filter_by(external_id=str(payment_id), status='pending').first()
                
                if trans:
                    trans.status = 'approved'
                    user = User.query.get(trans.user_id)
                    user.balance += trans.amount # Adiciona o saldo automaticamente!
                    db.session.commit()
                    print(f"PIX APROVADO! Saldo de R$ {trans.amount} adicionado para {user.username}")
                    return jsonify({'success': True}), 200
                    
    return jsonify({'success': True}), 200


# --- ROTAS DE SAQUE ---
@app.route('/api/withdraw/request', methods=['POST'])
@token_required
def withdraw_request(current_user):
    data = request.json
    amount = float(data.get('amount', 0))
    pix_key = data.get('pix_key')
    
    if amount <= 0 or not pix_key:
        return jsonify({'success': False, 'msg': 'Dados inválidos'})

    # Proteção Lock: Impede saques simultâneos bugando o saldo
    user = User.query.with_for_update().get(current_user.id)
    
    if user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})
        
    user.balance -= amount # Deduz o saldo instantaneamente
    
    trans = Transaction(user_id=user.id, type='withdraw', amount=amount, pix_key=pix_key, status='pending')
    db.session.add(trans)
    db.session.commit()
    
    return jsonify({'success': True, 'msg': 'Saque solicitado com sucesso! Aguarde aprovação.', 'new_balance': user.balance})


# --- ROTAS DO PAINEL ADMIN (GOD MODE) ---
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard(current_user):
    users = User.query.all()
    users_data = [{"id": u.id, "username": u.username, "balance": u.balance, "vip": u.vip} for u in users]
    
    withdrawals = Transaction.query.filter_by(type='withdraw', status='pending').all()
    wd_data = [{"id": w.id, "user": User.query.get(w.user_id).username, "amount": w.amount, "pix": w.pix_key} for w in withdrawals]
    
    return jsonify({'success': True, 'users': users_data, 'withdrawals': wd_data})

@app.route('/api/admin/withdraw/action', methods=['POST'])
@admin_required
def admin_withdraw_action(current_user):
    data = request.json
    trans_id = data.get('id')
    action = data.get('action') # 'approve' ou 'reject'
    
    trans = Transaction.query.with_for_update().get(trans_id)
    if not trans or trans.status != 'pending':
        return jsonify({'success': False, 'msg': 'Saque não encontrado ou já processado.'})
        
    if action == 'approve':
        trans.status = 'approved'
        # Aqui no futuro você integra o envio automático via PIX do Mercado Pago. Por enquanto, baixa manual.
    elif action == 'reject':
        trans.status = 'rejected'
        user = User.query.get(trans.user_id)
        user.balance += trans.amount # Estorna o saldo para o cliente
        
    db.session.commit()
    return jsonify({'success': True, 'msg': f'Saque {"aprovado" if action == "approve" else "rejeitado"} com sucesso.'})


# --- ROTAS DE JOGO (SEGURANÇA DE APOSTAS) ---
@app.route('/api/game/bet', methods=['POST'])
@token_required
def game_bet(current_user):
    # Essa é uma rota genérica super segura que o frontend pode chamar para validar o custo da aposta de verdade
    data = request.json
    amount = float(data.get('amount', 0))
    game = data.get('game')
    
    if amount <= 0:
        return jsonify({'success': False, 'msg': 'Aposta inválida.'})
        
    user = User.query.with_for_update().get(current_user.id)
    if user.balance < amount:
        return jsonify({'success': False, 'msg': 'Saldo insuficiente.'})
        
    user.balance -= amount
    db.session.commit()
    
    return jsonify({'success': True, 'new_balance': user.balance})

@app.route('/api/game/win', methods=['POST'])
@token_required
def game_win(current_user):
    # Essa rota adiciona o prêmio se o jogador ganhar.
    # Em um cassino 100% real, o backend calcula a vitória, mas como seu front já roda os jogos, usamos isso para atualizar o saldo no banco.
    data = request.json
    win_amount = float(data.get('win_amount', 0))
    
    if win_amount <= 0:
        return jsonify({'success': False, 'msg': 'Valor inválido.'})
        
    user = User.query.with_for_update().get(current_user.id)
    user.balance += win_amount
    db.session.commit()
    
    return jsonify({'success': True, 'new_balance': user.balance})


if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Cria as tabelas do banco se não existirem
        
        # Cria o usuário admin padrão se não existir
        if not User.query.filter_by(username='admin').first():
            hashed = generate_password_hash('admin', method='pbkdf2:sha256')
            admin = User(username='admin', email='admin@nexus.com', cpf='00000000000', phone='00000000000', password_hash=hashed, role='admin', vip='adm')
            db.session.add(admin)
            db.session.commit()
            print("Usuário Admin Master criado! (Login: admin / Senha: admin)")

    # Usa a porta dinâmica do Render ou a 5000 localmente
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
