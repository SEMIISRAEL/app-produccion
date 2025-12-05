import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from datetime import datetime, timedelta
import time
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from gspread.utils import rowcol_to_a1

# ==========================================
# --- 🛑 ZONA DE CONFIGURACIÓN GLOBAL (FIX V18) 🛑 ---
# ==========================================
# 1. IDs FIJOS (CONSTANTES)
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# 2. VARIABLES GLOBALES DE SESIÓN (INIT)
ID_ROSTER_ACTIVO = None
TRAMO_ACTIVO = None
ARCHIVO_PROD_ACTIVO = None

# 3. CONFIG DE PÁGINA
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# ==========================================
#           LOGIN
# ==========================================
def check_login():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.user_role = None
        st.session_state.user_name = None

    if not st.session_state.logged_in:
        st.markdown("<h1 style='text-align: center;'>🔐 Acceso Restringido</h1>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            usuario = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Entrar", type="primary", use_container_width=True):
                try:
                    users_db = st.secrets["usuarios"]
                    roles_db = st.secrets["roles"]
                    if usuario in users_db and users_db[usuario] == password:
                        st.session_state.logged_in = True
                        st.session_state.user_name = usuario
                        st.session_state.user_role = roles_db.get(usuario, "invitado")
                        st.rerun()
                    else: st.error("❌ Incorrecto")
                except: st.error("⚠️ Error Secrets")
        return False
    return True

if not check_login(): st.stop()

# ==========================================
#      SIDEBAR (CONFIGURACIÓN IZQUIERDA)
# ==========================================
@st.cache_data(ttl=300)
def buscar_archivos_roster():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
        service = build('drive', 'v3', credentials=creds)
        query = "name contains 'Roster' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)", orderBy="name desc").execute()
        return {f['name']: f['id'] for f in results.get('files', [])}
    except: return {}

@st.cache_data(ttl=600)
def cargar_config_prod():
    sh = conectar_flexible(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            if len(row) >= 3 and row[0] and row[1]: 
                tramo = row[0].strip()
                archivo_principal = row[1].strip()
                archivo_backup = row[2].strip() if len(row) > 2 else "" 
                if tramo and archivo_principal:
                    config[tramo] = (archivo_principal, archivo_backup)
            elif row and row[0].lower() in ["tramo", "seccion"]: continue 
        return config
    except: return {}

with st.sidebar:
    st.write(f"👤 **{st.session_state.user_name.upper()}**")
    if st.button("Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()
    st.markdown("---")
    
    # --- 1. ROSTER ---
    st.caption("📅 ROSTER ACTIVO")
    archivos_roster = buscar_archivos_roster()
    
    if archivos_roster:
        if st.session_state.user_role == "admin":
            st.header("🗂️ Configuración")
            nombre_roster_sel = st.selectbox("Archivo Horas:", list(archivos_roster.keys()))
        else:
            nombre_roster_sel = list(archivos_roster.keys())[0]
            
        ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
        st.success(f"Conectado: {nombre_roster_sel}")
    else: st.error("No hay Rosters.")
    
    st.markdown("---")

    # --- 2. TRAMO ---
    st.caption("🏗️ PROYECTO / TRAMO")
    conf_prod = cargar_config_prod()
    
    if conf_prod:
        tramo_sel = st.selectbox("Seleccionar Tramo:", list(conf_prod.keys()), index=None, placeholder="Elige...")
        if tramo_sel:
            # USAMOS VARIABLES GLOBALES
            TRAMO_ACTIVO = tramo_sel
            global ARCHIVO_PROD_ACTIVO # Necesario para reasignar variable global
            global ARCHIVO_PROD_BACKUP_ACTIVO
            ARCHIVO_PROD_ACTIVO, ARCHIVO_PROD_BACKUP_ACTIVO = conf_prod.get(tramo_sel)
            st.info(f"📁 {ARCHIVO_PROD_ACTIVO}")
    else:
        st.warning("Sin config producción")

# ==========================================
#           CONEXIÓN Y LÓGICA
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def conectar_flexible(referencia):
    client = get_gspread_client()
    try: return client.open_by_key(referencia)
    except:
        try: return client.open(referencia)
        except:
            try: return client.open(referencia.replace(".xlsx", ""))
            except: return None

# ==========================================
#      CARGA MASIVA (CACHÉ)
# ==========================================
@st.cache_data(ttl=300) 
def cargar_datos_completos_hoja(nombre_archivo, nombre_hoja):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try:
        ws = sh.worksheet(nombre_hoja)
        todos_los_datos = ws.get_all_values() 
        datos_procesados = {}
        for i, fila in enumerate(todos_los_datos):
            if not fila: continue
            item_id = str(fila[0]).strip()
            if len(item_id) > 2 and "ITEM" not in item_id.upper() and "HR TRACK" not in item_id.upper():
                datos_procesados[item_id] = {"fila_excel": i + 1, "datos": fila}
        return datos_procesados
    except: return None

def safe_val(lista, indice):
    idx_py = indice - 1
    if idx_py < len(lista): return lista[idx_py]
    return None

# ==========================================
#      CARGAS DATOS AUXILIARES
# ==========================================
@st.cache_data(ttl=600)
def cargar_vehiculos_dict():
    sh = conectar_flexible(ID_VEHICULOS)
    if not sh: return {}
    try:
        return {r[0]: (r[1] if len(r)>1 else "") for r in sh.sheet1.get_all_values() if r and r[0] and "veh" not in r[0].lower()}
    except: return {}

def cargar_trabajadores_disponibles(fecha_dt, id_roster):
    if not id_roster: return []
    sh = conectar_flexible(id_roster)
    if not sh: return []
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        datos = ws.get_all_values()
        lista = []
        col_dia = 14 
        hoy_dia = str(datetime.now().day)
        for r in range(3, 9):
            if r < len(datos) and hoy_dia in datos[r]: 
                col_dia = datos[r].index(hoy_dia)
                break
        
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid, nom = str(fila[0]).strip(), str(fila[1]).strip()
            if not uid or "id" in uid.lower(): continue
            if len(fila) > col_dia and fila[col_dia]: continue 
            
            tipo = "OBRA"
            if len(fila) > 2 and ("A" == str(fila[2]).upper() or "ALMACEN" in str(fila[2]).upper()): tipo = "ALMACEN"
            
            lista.append({"display": f"{uid} - {nom}", "tipo": tipo, "id": uid, "nombre_solo": nom})
        return lista
    except: return []

# ==========================================
#          GUARDADO Y ACTUALIZACIONES
# ==========================================
def buscar_columna_dia(ws, dia_num):
    header_rows = ws.get_values("E4:AX9") 
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            if val and (str(val).strip() == str(dia_num)): return c_idx + 5 
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

def guardar_parte(fecha, lista, vehiculo, para, id_roster):
    sh = conectar_flexible(id_roster)
    if not sh: return False
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        header = ws.range(f"E4:AX9")
        c_idx = next((c.col for c in header if str(c.value) == str(fecha.day)), 14)
        ids_col = ws.col_values(1)
        cells_to_update = []
        for t in lista:
            try: 
                r = ids_col.index(t['ID']) + 1
                cells_to_update.append(gspread.Cell(r, c_idx, t['Turno_Letra']))
                cells_to_update.append(gspread.Cell(r, c_idx+1, t['Total_Horas']))
            except: pass
        if cells_to_update: ws.update_cells(cells_to_update)
        if datos_paralizacion:
            try: wp = sh.worksheet("Paralizaciones")
            except: 
                wp = sh.add_worksheet("Paralizaciones", 1000, 10)
                wp.append_row(["Fecha", "Vehiculo/Lugar", "Inicio", "Fin", "Horas", "Motivo", "Usuario"])
            wp.append_row([str(fecha.date()), vehiculo, datos_paralizacion['inicio'], datos_paralizacion['fin'], datos_paralizacion['duracion'], datos_paralizacion['motivo'], st.session_state.user_name])
        return True
    except: return False

def guardar_prod_con_nota(archivo_principal, hoja, fila, col, valor, vehiculo, archivo_backup=None):
    exito_principal = False
    sh = conectar_flexible(archivo_principal)
    if not sh: return False
    
    try:
        ws = sh.worksheet(hoja)
        ws.update_cell(fila, col, valor)
        celda_a1 = rowcol_to_a1(fila, col)
        hora_act = datetime.now().strftime("%H:%M")
        nota = f"📅 {valor} - {hora_act}\n🚛 {vehiculo}\n👷 {st.session_state.user_name}"
        ws.insert_note(celda_a1, nota)
        exito_principal = True
        
    except Exception as e:
        st.error(f"❌ Error al escribir en hoja principal: {e}")
        return False
        
    if archivo_backup and archivo_backup != "":
        try:
            sh_bk = conectar_flexible(archivo_backup)
            if sh_bk:
                ws_bk = sh_bk.worksheet(hoja)
                ws_bk.update_cell(fila, col, valor)
                ws_bk.insert_note(rowcol_to_a1(fila, col), nota)
                st.toast("Copia de seguridad guardada.")
        except Exception as e:
            st.warning(f"⚠️ Falló el Backup: {e}")

    cargar_datos_completos_hoja.clear() 
    return exito_principal

# ==========================================
#          PDF GENERATOR
# ==========================================
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    start_time, end_time = "________", "________"
    if trabajadores: start_time, end_time = trabajadores[0]['H_Inicio'], trabajadores[0]['H_Fin']

    y = height - 90
    c.setLineWidth(1); c.rect(40, y - 60, 515, 70) 
    c.setFont("Helvetica-Bold", 16); c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10); c.drawString(400, height - 50, "Israel Railways Project")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y - 15, f"Date: {fecha_str}"); c.drawString(250, y - 15, f"Vehicle / Activity: {jefe}")
    c.drawString(50, y - 45, f"Start Time: {start_time}"); c.drawString(200, y - 45, f"End Time: {end_time}")
    c.drawString(350, y - 45, "Weather: ________")

    y_cursor = y - 80
    c.setFillColor(colors.HexColor("#2980B9")); c.rect(40, y_cursor, 515, 20, fill=1); c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    headers = ["Employee Name", "ID Number", "Company", "Profession", "Normal", "Extra", "Night"]
    x_coords = [40, 180, 260, 330, 400, 450, 500, 555]
    for i, head in enumerate(headers): c.drawString(x_coords[i] + 5, y_cursor + 6, head)
    
    y_cursor -= 20; c.setFillColor(colors.black); c.setFont("Helvetica", 9); y_tabla_start = y - 80
    for t in trabajadores:
        h_base = 8.0 if t['Total_Horas'] > 8 else t['Total_Horas']
        h_extra = t['Total_Horas'] - 8.0 if t['Total_Horas'] > 8 else 0.0
        col_base = 6 if t['Es_Noche'] else 4
        c.drawString(x_coords[0]+5, y_cursor+6, t['Nombre'][:25])
        c.drawString(x_coords[1]+5, y_cursor+6, str(t['ID']))
        c.drawString(x_coords[2]+5, y_cursor+6, "SEMI")
        c.drawString(x_coords[3]+5, y_cursor+6, "Official")
        c.drawString(x_coords[col_base]+10, y_cursor+6, f"{h_base:g}")
        if h_extra > 0: c.drawString(x_coords[5]+10, y_cursor+6, f"{h_extra:g}")
        c.setLineWidth(0.5); c.line(40, y_
