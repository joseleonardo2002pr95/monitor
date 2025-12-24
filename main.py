import requests
import json
import time
import re
import base64
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from supabase import create_client, Client

# --- CONFIGURAÇÕES ---
ID_INICIAL = 1
NOME_PLANILHA = "monitor_depositos"  # <--- ATUALIZADO AQUI
ARQUIVO_CREDENCIAIS = "credentials.json"

# ⚠️ ATENÇÃO: Atualize os cookies se o login cair
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
        
        # Separa o Payload
        payload_b64 = token_jws.split('.')[1]
        
        # Corrige Padding do Base64
        padding = len(payload_b64) % 4
        if padding > 0: payload_b64 += "=" * (4 - padding)
        
        # Decodifica JSON
        dados = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        
        # Extrai valor e chave
        val_str = dados.get('valor', {}).get('original', '0.00')
        chave = dados.get('chave', 'N/A')
        
        return float(val_str), chave
    except Exception as e:
        print(f"   [Erro Decode] {e}")
        return 0.0, "Erro Decode"

# --- BUSCA E EXTRAÇÃO ---
def buscar_valor_real(user_id):
    url_view = f"https://acebroker.io/traderoom/payin/3/{user_id}"
    try:
        r = requests.get(url_view, cookies=COOKIES, headers=HEADERS)
        
        # 1. Regex Cirúrgico para UUID (Formato novo do Pix)
        # Garante que pega apenas o link limpo, sem lixo no final
        regex_uuid = r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})'
        match = re.search(regex_uuid, r.text)
        
        gateway_url = ""
        token = ""

        if match:
            gateway_url = f"https://{match.group(1)}"
        else:
            # Fallback (Formato antigo ou sem UUID)
            fallback = re.search(r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-zA-Z0-9\-]+)', r.text)
            if fallback:
                # Corta manualmente antes do código '5204' (comum no Pix)
                raw = fallback.group(1).split('5204')[0]
                gateway_url = f"https://{raw}"
            else:
                return 0.0, "N/A", ""

        # 2. Baixa o Token do Gateway
        if gateway_url:
            r_gateway = requests.get(gateway_url)
            token = r_gateway.content.decode('utf-8').strip().replace('"', '')
            
            if "Error" in token or "<html" in token:
                 return 0.0, "Erro Gateway", gateway_url
            
            # 3. Decodifica
            val, chave = decodificar_jws_valor(token)
            return val, chave, gateway_url

    except Exception as e:
        return 0.0, str(e), ""
    
    return 0.0, "N/A", ""

# --- LOOP PRINCIPAL ---
def iniciar_monitoramento():
    print("🔌 Conectando ao Banco de Dados...")
    supabase = conectar_supabase()
    if not supabase: return

    # --- LÓGICA DE MEMÓRIA (RESUME) ---
    current_id = ID_INICIAL
    try:
        # Busca o último ID salvo no banco para continuar dele
        response = supabase.table("depositos").select("id").order("id", desc=True).limit(1).execute()
        if response.data and len(response.data) > 0:
            ultimo_id = response.data[0]['id']
            current_id = ultimo_id + 1
            print(f"🔄 Último registro encontrado: {ultimo_id}. Retomando do ID {current_id}...")
        else:
            print(f"🚀 Nenhum registro anterior. Iniciando do zero no ID {current_id}...")
    except Exception as e:
        print(f"⚠️ Erro ao buscar histórico: {e}. Usando ID padrão {ID_INICIAL}")

    # Loop Infinito
    while True:
        try:
            # 1. Verifica Status no AceBroker
            url_check = f"https://acebroker.io/traderoom/payin/checar/{current_id}"
            resp = requests.post(url_check, cookies=COOKIES, headers=HEADERS)
            
            try:
                data = resp.json()
            except:
                # Se der erro no JSON, espera um pouco e tenta o mesmo ID de novo
                time.sleep(2)
                continue

            status_code = str(data.get("status", ""))
            
            # SE EXISTE (0 = Pendente, 1 = Pago)
            if status_code in ["0", "1"]:
                status_text = "PAGO" if status_code == "1" else "PENDENTE"
                
                # Busca o valor real hackeando o Pix
                val_real, recebedor, link = buscar_valor_real(current_id)
                
                print(f"💰 ID {current_id} | {status_text} | R$ {val_real}")
                
                # Prepara dados
                dados_para_salvar = {
                    "id": current_id,
                    "status": status_text,
                    "valor_real": val_real,
                    "recebedor": recebedor,
                    "msg_sistema": data.get("msg", ""),
                    "link_gateway": link
                }
                
                # Salva no Supabase (Upsert atualiza se já existir)
                try:
                    supabase.table("depositos").upsert(dados_para_salvar).execute()
                except Exception as e_db:
                    print(f"❌ Erro ao salvar no DB: {e_db}")

                current_id += 1
                
            # SE É FUTURO (Erro = não existe ainda)
            elif status_code == "erro":
                print(f"💤 Aguardando novo depósito... (Checando ID {current_id})", end='\r')
                time.sleep(10) 
                continue # Continua no MESMO id
            
            else:
                current_id += 1

            # Pausa para evitar bloqueio
            time.sleep(0.5)

        except Exception as e:
            print(f"\n❌ Erro no Loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_monitoramento()
