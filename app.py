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

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs EXACTOS (COPIADOS DE TU DIAGNÓSTICO) ---
# Usamos IDs porque son infalibles, el nombre a veces falla por espacios.
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

FOLDER_PDF_DRIVE = "PARTES_PDF"

# ==========================================
#           CONEXIÓN
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# Función para conectar usando ID (Más segura)
def conectar_por_id(file_id):
    client = get_gspread_client()
    try:
        return client.open_by_key(file_id)
    except Exception as e:
        st.error(f"Error conectando al archivo ID {file_id}: {e}")
        return None

# Función para conectar por nombre (Para los archivos de producción dinámicos)
def conectar_por_nombre(nombre_archivo):
    client = get_gspread_client()
    try:
        return client.open(nombre_archivo)
    except Exception as e:
        return None

def subir_pdf_a_drive(pdf_buffer, nombre_archivo):
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        scopes = ['https://www.googleapis.com/auth/drive']
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
        service = build('drive', 'v3', credentials=creds)
        
        query = f"mimeType='application/vnd.google-apps.folder' and name='{FOLDER_PDF_DRIVE}' and trashed=false"
        results = service.files().list(q=query, fields="files(id)").execute()
        items = results.get('files', [])
        
        if not items:
            metadata = {'name': FOLDER_PDF_DRIVE, 'mimeType': 'application/vnd.google-apps.folder'}
            folder = service.files().create(body=metadata, fields='id').execute()
            folder_id = folder.get('id')
        else:
            folder_id = items[0]['id']
            
        file_metadata = {'name': nombre_archivo, 'parents': [folder_id]}
        media = MediaIoBaseUpload(pdf_buffer, mimetype='application/pdf', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except: return False

# ==========================================
#        LÓGICA DE CARGA DE DATOS
# ==========================================

# Usamos caché para que vaya rápido y no bloquee Google
@st.cache_data(ttl=600)
def cargar_vehiculos_dict():
    sh = conectar_por_id(ID_VEHICULOS)
    if not sh: return {}
    try:
        ws = sh.sheet1
        datos = ws.get_all_values()
        diccionario = {}
        for fila in datos:
            if not fila: continue
            nombre = str(fila[0]).strip()
            if nombre.lower() in ["nombre", "vehiculo", "vehicle", "nan", ""]: continue
            info = str(fila[1]).strip() if len(fila) > 1 else ""
            diccionario[nombre] = info
        return diccionario
    except: return {}

@st.cache_data(ttl=600)
def cargar_trabajadores():
    sh = conectar_por_id(ID_ROSTER)
    if not sh: return []
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
            
        datos = ws.get_all_values()
        lista_trabajadores = []
        
        # Leemos desde fila 9
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid = str(fila[0]).strip()
            nombre = str(fila[1]).strip()
            
            tipo = "OBRA"
            if len(fila) > 2:
                marca = str(fila[2]).strip().upper()
                if marca == "A" or "ALMACEN" in marca:
                    tipo = "ALMACEN"
            
            if uid and nombre and uid.lower() != "id":
                lista_trabajadores.append({
                    "display": f"{uid} - {nombre}",
                    "tipo": tipo,
                    "id": uid,
                    "nombre_solo": nombre
                })
        return lista_trabajadores
    except Exception as e:
        return []

def buscar_columna_dia(ws, dia_num):
    header_rows = ws.get_values("E4:AX9") 
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            if val and (str(val).strip() == str(dia_num)):
                return c_idx + 5 
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

# ==========================================
#          LÓGICA DE GUARDADO
# ==========================================

def guardar_parte_en_nube(fecha_dt, lista_trabajadores, vehiculo, datos_paralizacion):
    sh = conectar_por_id(ID_ROSTER)
    if not sh: return False
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
        
        col_dia = buscar_columna_dia(ws, fecha_dt.day)
        col_ids = ws.col_values(1)
        cells_to_update = []
        
        for t in lista_trabajadores:
            try:
                fila = col_ids.index(t['ID']) + 1 
                cells_to_update.append(gspread.Cell(fila, col_dia, t['Turno_Letra']))
                cells_to_update.append(gspread.Cell(fila, col_dia + 1, t['Total_Horas']))
            except: pass
                
        if cells_to_update: ws.update_cells(cells_to_update)

        if datos_paralizacion:
            try: ws_para = sh.worksheet("Paralizaciones")
            except: 
                ws_para = sh.add_worksheet("Paralizaciones", 1000, 10)
                ws_para.append_row(["Fecha", "Vehiculo/Lugar", "Inicio", "Fin", "Horas", "Motivo"])
            
            ws_para.append_row([
                str(fecha_dt.date()), vehiculo, datos_paralizacion['inicio'], 
                datos_paralizacion['fin'], datos_paralizacion['duracion'], datos_paralizacion['motivo']
            ])
        return True
    except Exception as e:
        st.error(f"Error al guardar: {e}")
        return False

# ==========================================
#          PRODUCCIÓN
# ==========================================

@st.cache_data(ttl=600)
def cargar_config_prod():
    sh = conectar_por_id(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            if len(row) >= 2 and row[0] and row[1]:
                config[row[0].strip()] = row[1].strip()
        return config
    except: return {}

def guardar_produccion(archivo_prod, hoja_prod, fila, col, valor):
    # Aquí usamos NOMBRE porque viene del Excel de config
    sh = conectar_por_nombre(archivo_prod)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja_prod)
        ws.update_cell(fila, col, valor)
        return True
    except Exception as e:
        st.error(f"Error guardando producción: {e}")
        return False

# ==========================================
#          PDF GENERATOR
# ==========================================
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 80, f"Date: {fecha_str} | Vehicle/Location: {jefe}")
    y = height - 120
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "ID - Name")
    c.drawString(300, y, "Time")
    c.drawString(400, y, "Total")
    c.drawString(450, y, "Shift")
    y -= 15
    c.setFont("Helvetica", 9)
    for t in trabajadores:
        c.drawString(50, y, f"{t['ID']} - {t['Nombre']}"[:45])
        c.drawString(300, y, f"{t['H_Inicio']} - {t['H_Fin']}")
        c.drawString(400, y, str(t['Total_Horas']))
        c.drawString(450, y, t['Turno_Letra'])
        y -= 15
        if y < 100: c.showPage(); y = height - 50
    if datos_para:
        y -= 20
        c.setFillColor(colors.red)
        c.drawString(50, y, f"⚠️ DELAY: {datos_para['motivo']} ({datos_para['duracion']}h)")
        c.setFillColor(colors.black)
        y -= 40
    if prod_dia:
        c.drawString(50, y - 20, "Production:")
        y -= 35
        for k, v in prod_dia.items():
            c.drawString(60, y, f"- {k}: {', '.join(v)}")
            y -= 15
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
#           INTERFAZ DE USUARIO
# ==========================================

if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

tab1, tab2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

# ---------------- PESTAÑA 1 ----------------
with tab1:
    st.subheader("Datos Generales")
    c_f1, c_f2, c_f3, c_veh, c_info = st.columns([1, 1, 1, 2, 2])
    
    hoy = datetime.now()
    dia = c_f1.selectbox("Día", [i for i in range(1, 32)], index=hoy.day-1)
    mes = c_f2.selectbox("Mes", [i for i in range(1, 13)], index=hoy.month-1)
    ano = c_f3.selectbox("Año", [2024, 2025, 2026], index=1)
    try: fecha_sel = datetime(ano, mes, dia)
    except: fecha_sel = hoy; st.error("Fecha incorrecta")

    dicc_vehiculos = cargar_vehiculos_dict()
    if dicc_vehiculos:
        nombres_veh = [""] + list(dicc_vehiculos.keys())
        vehiculo_sel = c_veh.selectbox("Vehículo / Lugar", nombres_veh)
        info_extra = dicc_vehiculos.get(vehiculo_sel, "")
        c_info.text_input("Detalle", value=info_extra, disabled=True)
    else:
        vehiculo_sel = c_veh.selectbox("Vehículo / Lugar", ["Error Carga"])
        c_info.text_input("Detalle", disabled=True)
        
    st.divider()
    
    st.write("**Filtrar Personal:**")
    filtro = st.radio("Filtro", ["TODOS", "OBRA", "ALMACEN"], horizontal=True, label_visibility="collapsed")
    
    c_add1, c_add2, c_add3, c_add4, c_add5 = st.columns([3, 1, 1, 1, 1])
    
    todos_trabajadores = cargar_trabajadores()
    
    if filtro == "ALMACEN":
        filtrados = [t for t in todos_trabajadores if t['tipo'] == "ALMACEN"]
        default_comida = True 
    elif filtro == "OBRA":
        filtrados = [t for t in todos_trabajadores if t['tipo'] != "ALMACEN"]
        default_comida = False
    else:
        filtrados = todos_trabajadores
        default_comida = False
        
    if not filtrados:
        opciones_nombres = ["Sin resultados"]
    else:
        opciones_nombres = [""] + [t['display'] for t in filtrados]
        
    trabajador_sel = c_add1.selectbox("Seleccionar Operario", opciones_nombres)
    
    h_ini_def = datetime.strptime("07:00", "%H:%M").time()
    h_fin_def = datetime.strptime("16:00", "%H:%M").time()
    h_ini = c_add2.time_input("Inicio", h_ini_def)
    h_fin = c_add3.time_input("Fin", h_fin_def)
    turno_manual = c_add4.selectbox("Turno", ["AUT", "D", "N"])
    desc_comida = c_add5.checkbox("-1h Comida", value=default_comida)
    
    if st.button("➕ AÑADIR A LA LISTA", use_container_width=True, type="secondary"):
        if trabajador_sel and trabajador_sel != "Sin resultados" and trabajador_sel != "":
            t_i = datetime.combine(fecha_sel, h_ini)
            t_f = datetime.combine(fecha_sel, h_fin)
            if t_f < t_i: t_f += timedelta(days=1)
            horas = (t_f - t_i).total_seconds() / 3600
            
            es_noche, t_letra = False, "D"
            if turno_manual == "N" or (turno_manual=="AUT" and (h_ini.hour>=21 or h_ini.hour<=
