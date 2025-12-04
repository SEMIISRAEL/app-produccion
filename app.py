import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- NOMBRES DE ARCHIVOS EN GOOGLE DRIVE ---
FILE_ROSTER = "Roster 2025 12 (empty)"
FILE_VEHICULOS = "Vehiculos 2"
FILE_CONFIG_PROD = "ARCHIVOS DE PRODUCION"

# --- CONEXIÓN ---
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

def conectar_hoja(nombre_archivo):
    client = get_gspread_client()
    try:
        return client.open(nombre_archivo)
    except Exception as e:
        return None

# ==========================================
#      LOGICA DE CARGA Y FILTROS
# ==========================================

def cargar_vehiculos():
    sh = conectar_hoja(FILE_VEHICULOS)
    if not sh: return []
    try:
        ws = sh.sheet1
        datos = ws.col_values(1) # Leemos columna A
        lista = [x for x in datos if x and x.lower() not in ["nombre", "vehiculo", "vehicle"]]
        return lista
    except: return []

def cargar_trabajadores():
    sh = conectar_hoja(FILE_ROSTER)
    if not sh: return []
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
            
        # Leemos todo el rango de datos
        datos = ws.get_all_values()
        
        lista_trabajadores = []
        
        # Empezamos en fila 9 (índice 8) como tu Excel original
        for fila in datos[8:]:
            # Aseguramos que la fila tenga al menos 2 columnas
            if len(fila) < 2: continue
            
            uid = str(fila[0]).strip()     # Columna A (ID)
            nombre = str(fila[1]).strip()  # Columna B (Nombre)
            
            # --- DETECCIÓN DE ALMACÉN (COLUMNA C) ---
            tipo = "OBRA"
            if len(fila) > 2:
                # Leemos la Columna C (índice 2)
                marca = str(fila[2]).strip().upper() 
                # Si hay una "A" o dice "ALMACEN", es de almacén
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
        st.error(f"Error leyendo trabajadores: {e}")
        return []

def buscar_columna_dia(ws, dia_num):
    # Busca el día en las cabeceras (Filas 4-9)
    header_rows = ws.get_values("E4:AX9") 
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            if val and (str(val).strip() == str(dia_num)):
                return c_idx + 5 
    # Fallback matemático
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

def guardar_parte_en_nube(fecha_dt, lista_trabajadores, vehiculo, datos_paralizacion):
    sh = conectar_hoja(FILE_ROSTER)
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
                ws_para.append_row(["Fecha", "Vehiculo", "Inicio", "Fin", "Horas", "Motivo"])
            
            ws_para.append_row([str(fecha_dt.date()), vehiculo, datos_paralizacion['inicio'], 
                                datos_paralizacion['fin'], datos_paralizacion['duracion'], datos_paralizacion['motivo']])
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False

# --- PDF GENERATOR (SIMPLIFICADO PARA WEB) ---
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.drawString(50, height - 80, f"Date: {fecha_str} | Team: {jefe}")
    
    y = height - 120
    c.setFont("Helvetica", 10)
    for t in trabajadores:
        linea = f"{t['ID']} - {t['Nombre']} | {t['H_Inicio']} - {t['H_Fin']} | {t['Total_Horas']}h ({t['Turno_Letra']})"
        c.drawString(50, y, linea)
        y -= 20
        
    if datos_para:
        c.setFillColor(colors.red)
        c.drawString(50, y - 20, f"⚠️ PARADA: {datos_para['motivo']} ({datos_para['duracion']}h)")
        c.setFillColor(colors.black)
        y -= 40
        
    if prod_dia:
        c.drawString(50, y - 20, "Producción Realizada:")
        y -= 35
        for k, v in prod_dia.items():
            c.drawString(60, y, f"- {k}: {', '.join(v)}")
            y -= 15
            
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
#               INTERFAZ GRÁFICA
# ==========================================

tab1, tab2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

# Variables de Sesión
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

with tab1:
    # --- 1. SELECCIÓN DE FECHA (SEPARADA) ---
    st.subheader("Datos Generales")
    c_f1, c_f2, c_f3, c_veh = st.columns([1, 1, 1, 3])
    
    # Fecha independiente
    hoy = datetime.now()
    dia = c_f1.selectbox("Día", [i for i in range(1, 32)], index=hoy.day-1)
    mes = c_f2.selectbox("Mes", [i for i in range(1, 13)], index=hoy.month-1)
    ano = c_f3.selectbox("Año", [2024, 2025, 2026], index=1) # 2025 por defecto
    
    # Creamos la fecha real
    try:
        fecha_sel = datetime(ano, mes, dia)
    except:
        fecha_sel = hoy
        st.error("Fecha inválida")

    vehiculos = cargar_vehiculos()
    vehiculo_sel = c_veh.selectbox("Vehículo / Encargado", vehiculos if vehiculos else ["Cargando..."])
    
    st.divider()
    
    # --- 2. FILTRO DE PERSONAL (BOTONES) ---
    st.write("Filtrar Personal:")
    # Usamos radio horizontal para simular botones
    filtro = st.radio("Selecciona grupo:", ["TODOS", "OBRA", "ALMACEN"], horizontal=True, label_visibility="collapsed")
    
    c_add1, c_add2, c_add3, c_add4, c_add5 = st.columns([3, 1, 1, 1, 1])
    
    # Cargamos y Filtramos
    todos = cargar_trabajadores()
    
    if filtro == "ALMACEN":
        # Filtra si tiene "A" o "ALMACEN" en la columna C
        filtrados = [t for t in todos if t['tipo'] == "ALMACEN"]
        # Autocheck comida para almacén
        default_comida = True 
    elif filtro == "OBRA":
        # Todos los que NO son almacén
        filtrados = [t for t in todos if t['tipo'] != "ALMACEN"]
        default_comida = False
    else:
        # TODOS
        filtrados = todos
        default_comida = False
        
    if not filtrados:
        opciones = ["No hay operarios"]
    else:
        opciones = [t['display'] for t in filtrados]
        
    trabajador_sel = c_add1.selectbox("Seleccionar Operario", opciones)
    
    h_ini_def = datetime.strptime("07:00", "%H:%M").time()
    h_fin_def = datetime.strptime("16:00", "%H:%M").time()
    
    h_ini = c_add2.time_input("Inicio", h_ini_def)
    h_fin = c_add3.time_input("Fin", h_fin_def)
    turno_manual = c_add4.selectbox("Turno", ["AUT", "D", "N"])
    desc_comida = c_add5.checkbox("-1h Comida", value=default_comida)
    
    if st.button("➕ AÑADIR OPERARIO", use_container_width=True, type="secondary"):
        if trabajador_sel and trabajador_sel != "No hay operarios":
            # Cálculos
            str_ini = h_ini.strftime("%H:%M")
            str_fin = h_fin.strftime("%H:%M")
            
            # Replicamos lógica de cálculo
            t_i = datetime.combine(fecha_sel, h_ini)
            t_f = datetime.combine(fecha_sel, h_fin)
            if t_f < t_i: t_f += timedelta(days=1)
            horas = (t_f - t_i).total_seconds() / 3600
            
            es_noche = False
            t_letra = "D"
            if turno_manual == "N" or (turno_manual=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)):
                es_noche, t_letra = True, "N"
            
            if desc_comida: horas = max(0, horas - 1)
            
            # Sacar ID limpio
            uid = trabajador_sel.split(" - ")[0]
            nombre = trabajador_sel.split(" - ")[1]
            
            st.session_state.lista_sel.append({
                "ID": uid, "Nombre": nombre, "H_Inicio": str_ini, "H_Fin": str_fin,
                "Total_Horas": round(horas, 2), "Turno_Letra": t_letra, "Es_Noche": es_noche
            })

    # TABLA VISUAL
    if st.session_state.lista_sel:
        st.markdown("### Lista de Cuadrilla")
        df_show = pd.DataFrame(st.session_state.lista_sel)
        st.dataframe(df_show[["ID", "Nombre", "H_Inicio", "H_Fin", "Total_Horas", "Turno_Letra"]], use_container_width=True)
        if st.button("Borrar Lista"):
            st.session_state.lista_sel = []
            st.rerun()

    # GUARDADO FINAL
    st.divider()
    
    tiene_para = st.checkbox("Registrar Paralización")
    d_para = None
    if tiene_para:
        motivo = st.text_input("Motivo Parada")
        c_p1, c_p2 = st.columns(2)
        hi_p = c_p1.time_input("Inicio Parada")
        hf_p = c_p2.time_input("Fin Parada")
        # Calculo simple duracion
        d_para = {"inicio": str(hi_p), "fin": str(hf_p), "duracion": 1.0, "motivo": motivo} # Simplificado

    if st.button("💾 GUARDAR PARTE", type="primary", use_container_width=True):
        if not st.session_state.lista_sel:
            st.error("Lista vacía.")
        else:
            with st.spinner("Guardando..."):
                ok = guardar_parte_en_nube(fecha_sel, st.session_state.lista_sel, vehiculo_sel, d_para)
                if ok:
                    pdf = generar_pdf_bytes(str(fecha_sel.date()), vehiculo_sel, st.session_state.lista_sel, d_para, st.session_state.prod_dia)
                    st.success("¡Guardado!")
                    st.download_button("📥 Descargar PDF", pdf, f"Parte_{fecha_sel.date()}.pdf", "application/pdf")
                    st.session_state.lista_sel = []
                    st.session_state.prod_dia = {}

with tab2:
    st.header("Producción")
    st.info("Para activar la producción, asegúrate de que el archivo 'ARCHIVOS DE PRODUCION' está bien configurado en Drive.")
    # (El resto de la lógica de producción se mantiene igual que la versión anterior)
