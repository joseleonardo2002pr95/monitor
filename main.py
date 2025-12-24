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

# --- CONEXÃO COM GOOGLE SHEETS ---
def conectar_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(ARQUIVO_CREDENCIAIS, scope)
    client = gspread.authorize(creds)
    # Abre a planilha pelo nome exato
    sheet = client.open(NOME_PLANILHA).sheet1
    return sheet

# --- FUNÇÕES DE DECODIFICAÇÃO ---
def decodificar_jws_valor(token_jws):
    try:
        if '.' not in token_jws: return "Erro Token", "N/A"
        payload_b64 = token_jws.split('.')[1]
        padding = len(payload_b64) % 4
        if padding > 0: payload_b64 += "=" * (4 - padding)
        dados = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        val = dados.get('valor', {}).get('original', '0.00')
        chave = dados.get('chave', 'N/A')
        return val, chave
    except Exception as e: return f"Erro: {str(e)}", "N/A"

def buscar_valor_real(user_id):
    url_view = f"https://acebroker.io/traderoom/payin/3/{user_id}"
    try:
        r = requests.get(url_view, cookies=COOKIES, headers=HEADERS)
        
        # Regex para UUID (formato novo do Pix)
        regex_uuid = r'(pix\.onlyup\.com\.br\/qr\/v3\/at\/[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})'
        match = re.search(regex_uuid, r.text)
        
        if match:
            gateway_url = f"https://{match.group(1)}"
            r_gateway = requests.get(gateway_url)
            token = r_gateway.content.decode('utf-8').strip().replace('"', '')
            if "Error" in token or "<html" in token: return "Erro Gateway", "N/A", gateway_url
            return decodificar_jws_valor(token) + (gateway_url,)
        
        # Fallback (formato antigo)
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
        # Se a planilha estiver vazia, cria o cabeçalho
        if not sheet.get_all_values():
            sheet.append_row(['Data Hora', 'ID', 'Status', 'VALOR REAL (R$)', 'Recebedor', 'Msg', 'Link'])
        print(f"✅ Conectado na planilha: {NOME_PLANILHA}")
    except Exception as e:
        print(f"❌ Erro Crítico ao conectar no Sheets: {e}")
        print("   -> Verifique se o nome da planilha no Google está IGUAL: 'monitor_depositos'")
        print("   -> Verifique se você compartilhou a planilha com o email do credentials.json")
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
                
                try:
                    sheet.append_row([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        current_id, status_text, val_real, recebedor, data.get("msg", ""), link
                    ])
                except Exception as e_sheet:
                    print(f"⚠️ Erro ao salvar no Sheets (Tentando reconectar): {e_sheet}")
                    try:
                        sheet = conectar_sheets()
                        sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_id, status_text, val_real, recebedor, data.get("msg", ""), link])
                    except:
                        pass

                current_id += 1
                
            elif status_code == "erro":
                print(f"💤 Aguardando... (ID {current_id})", end='\r')
                time.sleep(10)
                continue 
            else:
                current_id += 1

            time.sleep(1)

        except Exception as e:
            print(f"❌ Erro Loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_monitoramento()
