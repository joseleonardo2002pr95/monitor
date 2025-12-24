import requests
import json
import time
import re
import base64
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- CONFIGURAÇÕES ---
ID_INICIAL = 1
NOME_PLANILHA = "Fluxo Caixa Monitor"  # Tem que ser IDÊNTICO ao nome no Google Sheets
ARQUIVO_CREDENCIAIS = "credentials.json"

# COOKIES (Mantenha atualizado!)
COOKIES = {
    'authi': 'a8fb903cf884f7552b8e5d858652e3d2',
    'PHPSESSID': 'dnb8poau59t7hk2nnnu3vg196b',
    'email': 'seu_email%40gmail.com'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'X-Requested-With': 'XMLHttpRequest'
}

# --- CONEXÃO COM GOOGLE SHEETS ---
def conectar_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(ARQUIVO_CREDENCIAIS, scope)
    client = gspread.authorize(creds)
    sheet = client.open(NOME_PLANILHA).sheet1
    return sheet

# --- FUNÇÕES DE DECODIFICAÇÃO (IGUAIS AO ANTERIOR) ---
def decodificar_jws_valor(token_jws):
    try:
        if '.' not in token_jws: return "Erro Token", "N/A"
        payload_b64 = token_jws.split('.')[1]
        padding = len(payload_b64) % 4
        if padding > 0: payload_b64 += "=" * (4 - padding)
        dados = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        return dados.get('valor', {}).get('original', '0.00'), dados.get('chave', 'N/A')
    except Exception as e: return f"Erro: {e}", "N/A"

def buscar_valor_real(user_id):
    url_view = f"https://acebroker.io/traderoom/payin/3/{user_id}"
    try:
        r = requests.get(url_view, cookies=COOKIES, headers=HEADERS)
        # Regex UUID corrigido
        regex_uuid = r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})'
        match = re.search(regex_uuid, r.text)
        
        if match:
            gateway_url = f"https://{match.group(1)}"
            r_gateway = requests.get(gateway_url)
            token = r_gateway.content.decode('utf-8').strip().replace('"', '')
            if "Error" in token or "<html" in token: return "Erro Gateway", "N/A", gateway_url
            return decodificar_jws_valor(token) + (gateway_url,)
        
        # Fallback
        fallback = re.search(r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-zA-Z0-9\-]+)', r.text)
        if fallback:
            gateway_url = f"https://{fallback.group(1).split('5204')[0]}"
            r_gateway = requests.get(gateway_url)
            token = r_gateway.content.decode('utf-8').strip().replace('"', '')
            return decodificar_jws_valor(token) + (gateway_url,)
            
        return "N/A", "N/A", ""
    except Exception as e: return "Erro", str(e), ""

# --- LOOP PRINCIPAL ---
def iniciar_monitoramento():
    print("🔌 Conectando ao Google Sheets...")
    try:
        sheet = conectar_sheets()
        # Adiciona cabeçalho se a planilha estiver vazia
        if not sheet.get_all_values():
            sheet.append_row(['Data Hora', 'ID', 'Status', 'VALOR REAL (R$)', 'Recebedor', 'Msg', 'Link'])
        print("✅ Conectado com Sucesso!")
    except Exception as e:
        print(f"❌ Erro ao conectar no Sheets: {e}")
        return

    current_id = ID_INICIAL
    print(f"🚀 Iniciando Auditoria no ID {current_id}...")

    while True:
        try:
            url_check = f"https://acebroker.io/traderoom/payin/checar/{current_id}"
            resp = requests.post(url_check, cookies=COOKIES, headers=HEADERS)
            
            try:
                data = resp.json()
            except:
                time.sleep(2)
                continue

            status_code = str(data.get("status", ""))
            
            if status_code in ["0", "1"]:
                status_text = "✅ PAGO" if status_code == "1" else "⏳ PENDENTE"
                val_real, recebedor, link = buscar_valor_real(current_id)
                
                print(f"💰 ID {current_id} | {status_text} | R$ {val_real}")
                
                # --- ENVIA PARA O GOOGLE SHEETS ---
                try:
                    sheet.append_row([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        current_id, status_text, val_real, recebedor, data.get("msg", ""), link
                    ])
                except Exception as e_sheet:
                    print(f"⚠️ Erro ao salvar no Sheets (Tentando reconectar): {e_sheet}")
                    # Tenta reconectar e salvar de novo
                    try:
                        sheet = conectar_sheets()
                        sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_id, status_text, val_real, recebedor, data.get("msg", ""), link])
                    except:
                        print("❌ Falha crítica ao salvar linha.")

                current_id += 1
                
            elif status_code == "erro":
                print(f"💤 Aguardando... (ID {current_id})", end='\r')
                time.sleep(10)
                continue # Tenta o mesmo ID de novo
            else:
                current_id += 1

            time.sleep(1) # Respeita o limite da API do Google

        except Exception as e:
            print(f"❌ Erro Loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_monitoramento()
