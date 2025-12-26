import requests
import json
import time
import re
import base64
import os
from datetime import datetime
from supabase import create_client, Client

# --- CONFIGURAÇÕES ---
ID_INICIAL = 1
QTD_RETROVISOR = 5  # Quantos IDs para trás ele vai verificar

# Variáveis de Ambiente (Railway)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# ⚠️ COOKIES (Mantidos os que você enviou)
COOKIES = {
    'authi': '2440b5dcbbe40cc7140ad9830c0d62b5',
    'PHPSESSID': 'fentmnq0rrc4lf60frjij113h7',
    'email': 'alberto0codug%40gmail.com'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'X-Requested-With': 'XMLHttpRequest'
}

# --- CONEXÃO COM SUPABASE ---
def conectar_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ ERRO: Variáveis SUPABASE_URL ou SUPABASE_KEY não encontradas.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"❌ Erro ao conectar no Supabase: {e}")
        return None

# --- DECODIFICAÇÃO (JWS) ---
def decodificar_jws_valor(token_jws):
    try:
        if '.' not in token_jws: return 0.0, "N/A"
        payload_b64 = token_jws.split('.')[1]
        padding = len(payload_b64) % 4
        if padding > 0: payload_b64 += "=" * (4 - padding)
        dados = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        val_str = dados.get('valor', {}).get('original', '0.00')
        chave = dados.get('chave', 'N/A')
        return float(val_str), chave
    except Exception as e:
        return 0.0, "Erro Decode"

# --- BUSCA E EXTRAÇÃO ---
def buscar_valor_real(user_id):
    url_view = f"https://acebroker.io/traderoom/payin/3/{user_id}"
    try:
        r = requests.get(url_view, cookies=COOKIES, headers=HEADERS)
        
        # Regex UUID
        regex_uuid = r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})'
        match = re.search(regex_uuid, r.text)
        
        gateway_url = ""

        if match:
            gateway_url = f"https://{match.group(1)}"
        else:
            # Fallback
            fallback = re.search(r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-zA-Z0-9\-]+)', r.text)
            if fallback:
                raw = fallback.group(1).split('5204')[0]
                gateway_url = f"https://{raw}"
            else:
                return 0.0, "N/A", ""

        if gateway_url:
            r_gateway = requests.get(gateway_url)
            token = r_gateway.content.decode('utf-8').strip().replace('"', '')
            if "Error" in token or "<html" in token:
                 return 0.0, "Erro Gateway", gateway_url
            
            val, chave = decodificar_jws_valor(token)
            return val, chave, gateway_url

    except Exception as e:
        return 0.0, str(e), ""
    
    return 0.0, "N/A", ""

# --- NOVA FUNÇÃO: O RETROVISOR ---
def revisar_ids_anteriores(id_atual, supabase):
    """
    Volta X casas para trás e verifica se o status mudou (ex: de Pendente para Pago).
    """
    start = max(1, id_atual - QTD_RETROVISOR) # Garante que não busca ID negativo
    print(f"   ↪️  Revisando IDs anteriores: {start} até {id_atual - 1}...")

    for check_id in range(start, id_atual):
        try:
            url_check = f"https://acebroker.io/traderoom/payin/checar/{check_id}"
            resp = requests.post(url_check, cookies=COOKIES, headers=HEADERS)
            data = resp.json()
            status_code = str(data.get("status", ""))

            # Se for status 1 (PAGO), a gente força a atualização no banco
            if status_code == "1":
                print(f"      ✨ ATUALIZAÇÃO: ID {check_id} agora está PAGO! Salvando...")
                
                # Busca os dados completos novamente para garantir
                val_real, recebedor, link = buscar_valor_real(check_id)
                
                dados_update = {
                    "id": check_id,
                    "status": "PAGO", # Atualiza status
                    "valor_real": val_real,
                    "recebedor": recebedor,
                    "msg_sistema": data.get("msg", ""),
                    "link_gateway": link
                }
                supabase.table("depositos").upsert(dados_update).execute()
            
            # Pequeno delay para não sobrecarregar
            time.sleep(0.2)

        except Exception as e:
            pass # Se der erro na revisão, apenas ignora e segue

# --- LOOP PRINCIPAL ---
def iniciar_monitoramento():
    print("🔌 Conectando ao Banco de Dados...")
    supabase = conectar_supabase()
    if not supabase: return

    current_id = ID_INICIAL
    
    # Tenta continuar de onde parou
    try:
        response = supabase.table("depositos").select("id").order("id", desc=True).limit(1).execute()
        if response.data and len(response.data) > 0:
            current_id = response.data[0]['id'] + 1
            print(f"🔄 Retomando do ID {current_id}...")
        else:
            print(f"🚀 Iniciando do ID base {current_id}...")
    except Exception as e:
        current_id = ID_INICIAL

    while True:
        try:
            # 1. Verifica ID Atual (O Futuro)
            url_check = f"https://acebroker.io/traderoom/payin/checar/{current_id}"
            resp = requests.post(url_check, cookies=COOKIES, headers=HEADERS)
            
            try:
                data = resp.json()
            except:
                time.sleep(2)
                continue

            status_code = str(data.get("status", ""))
            
            # SE EXISTE (Novo Depósito Encontrado)
            if status_code in ["0", "1"]:
                status_text = "PAGO" if status_code == "1" else "PENDENTE"
                val_real, recebedor, link = buscar_valor_real(current_id)
                
                print(f"💰 ID {current_id} | {status_text} | R$ {val_real}")
                
                dados_para_salvar = {
                    "id": current_id,
                    "status": status_text,
                    "valor_real": val_real,
                    "recebedor": recebedor,
                    "msg_sistema": data.get("msg", ""),
                    "link_gateway": link
                }
                
                try:
                    supabase.table("depositos").upsert(dados_para_salvar).execute()
                except Exception as e_db:
                    print(f"❌ Erro ao salvar: {e_db}")

                # --- AQUI ESTÁ A MÁGICA: REVISÃO ---
                # Depois de processar um ID com sucesso, revisa os anteriores
                revisar_ids_anteriores(current_id, supabase)
                
                current_id += 1
                
            # SE AINDA NÃO EXISTE (Fim da fila)
            elif status_code == "erro":
                print(f"💤 Aguardando novo depósito... (ID {current_id})", end='\r')
                
                # DICA: Enquanto espera o novo, revisa os antigos também!
                # Isso garante que se o site estiver parado, a gente continua checando pagamentos.
                revisar_ids_anteriores(current_id, supabase)
                
                time.sleep(5) 
                continue 
            
            else:
                current_id += 1

            time.sleep(0.5)

        except Exception as e:
            print(f"\n❌ Erro Loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_monitoramento()
