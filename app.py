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

# --- CONFIGURACIÓN DE PÁGINA (Debe ser la primera línea) ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- NOMBRES EXACTOS DE ARCHIVOS EN GOOGLE DRIVE ---
FILE_ROSTER = "Roster 2025 12 (empty)"
FILE_VEHICULOS = "Vehiculos 2"
FILE_CONFIG_PROD = "ARCHIVOS DE PRODUCION"

# ==========================================
#           CONEXIÓN Y SEGURIDAD
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # Lee la llave secreta desde Streamlit
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
#        LÓGICA DE CARGA DE DATOS
# ==========================================

def cargar_vehiculos_dict():
    """Devuelve un diccionario {Matricula: Info_Extra}"""
    sh = conectar_hoja(FILE_VEHICULOS)
    if not sh: return {}
    try:
        ws = sh.sheet1
        datos = ws.get_all_values()
        diccionario = {}
        for fila in datos:
            if not fila: continue
            nombre = str(fila[0]).strip()
            # Filtramos cabeceras
            if nombre.lower() in ["nombre", "vehiculo", "vehicle", "nan", ""]: continue
            
            # Leemos la info extra de la Columna B (índice 1) si existe
            info = str(fila[1]).strip() if len(fila) > 1 else ""
            diccionario[nombre] = info
        return diccionario
    except: return {}

def cargar_trabajadores():
    sh = conectar_hoja(FILE_ROSTER)
    if not sh: return []
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
            
        # Leemos todos los datos para procesar en memoria (más rápido)
        datos = ws.get_all_values()
        lista_trabajadores = []
        
        # Empezamos a leer desde la fila 9 (índice 8) según tu formato
        for fila in datos[8:]:
            if len(fila) < 2: continue
            
            uid = str(fila[0]).strip()    # Col A: ID
            nombre = str(fila[1]).strip() # Col B: Nombre
            
            # --- DETECCIÓN AUTOMÁTICA DE ALMACÉN ---
            # Miramos la Columna C (índice 2) buscando una "A"
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
        st.error(f"Error leyendo roster: {e}")
        return []

def buscar_columna_dia(ws, dia_num):
    # Busca el número del día en las cabeceras (Filas 4 a 9)
    header_rows = ws.get_values("E4:AX9") 
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            if val and (str(val).strip() == str(dia_num)):
                # c_idx es relativo a la col E (5), así que sumamos 5
                return c_idx + 5 
    
    # Si falla la búsqueda visual, usa cálculo matemático por defecto
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

# ==========================================
#          LÓGICA DE GUARDADO (CLOUD)
# ==========================================

def guardar_parte_en_nube(fecha_dt, lista_trabajadores, vehiculo, datos_paralizacion):
    sh = conectar_hoja(FILE_ROSTER)
    if not sh: return False
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
        
        # 1. Buscar columna del día
        col_dia = buscar_columna_dia(ws, fecha_dt.day)
        
        # 2. Preparar actualizaciones en lote (Batch Update)
        col_ids = ws.col_values(1) # Leemos columna A para buscar filas
        cells_to_update = []
        
        for t in lista_trabajadores:
            try:
                # Buscamos la fila del ID (+1 porque gspread empieza en 1)
                fila = col_ids.index(t['ID']) + 1 
                
                # Turno en la columna del día
                cells_to_update.append(gspread.Cell(fila, col_dia, t['Turno_Letra']))
                # Horas en la columna siguiente (día + 1)
                cells_to_update.append(gspread.Cell(fila, col_dia + 1, t['Total_Horas']))
            except:
                pass # Si no encuentra el ID, lo salta
                
        if cells_to_update:
            ws.update_cells(cells_to_update)

        # 3. Guardar Paralización (si existe)
        if datos_paralizacion:
            try: ws_para = sh.worksheet("Paralizaciones")
            except: 
                ws_para = sh.add_worksheet("Paralizaciones", 1000, 10)
                ws_para.append_row(["Fecha", "Vehiculo", "Inicio", "Fin", "Horas", "Motivo"])
            
            ws_para.append_row([
                str(fecha_dt.date()), 
                vehiculo, 
                datos_paralizacion['inicio'], 
                datos_paralizacion['fin'], 
                datos_paralizacion['duracion'], 
                datos_paralizacion['motivo']
            ])
            
        return True
    except Exception as e:
        st.error(f"Error al guardar: {e}")
        return False

# ==========================================
#          LÓGICA DE PRODUCCIÓN
# ==========================================

def cargar_config_prod():
    sh = conectar_hoja(FILE_CONFIG_PROD)
    if not sh: return {}
    try:
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            # Asume Col A = Nombre Tramo, Col B = Nombre Archivo Excel
            if len(row) >= 2 and row[0] and row[1]:
                config[row[0].strip()] = row[1].strip()
        return config
    except: return {}

def guardar_produccion(archivo_prod, hoja_prod, fila, col, valor):
    sh = conectar_hoja(archivo_prod)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja_prod)
        ws.update_cell(fila, col, valor)
        return True
    except Exception as e:
        st.error(f"Error guardando producción: {e}")
        return False

# ==========================================
#          GENERADOR DE PDF (ReportLab)
# ==========================================
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    
    # Cabecera
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 80, f"Date: {fecha_str} | Vehicle/Team: {jefe}")
    
    y = height - 120
    
    # Tabla Trabajadores
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "ID - Name")
    c.drawString(300, y, "Time")
    c.drawString(400, y, "Total")
    c.drawString(450, y, "Shift")
    y -= 15
    c.setFont("Helvetica", 9)
    
    for t in trabajadores:
        txt_trab = f"{t['ID']} - {t['Nombre']}"
        txt_time = f"{t['H_Inicio']} - {t['H_Fin']}"
        
        c.drawString(50, y, txt_trab[:45])
        c.drawString(300, y, txt_time)
        c.drawString(400, y, str(t['Total_Horas']))
        c.drawString(450, y, t['Turno_Letra'])
        y -= 15
        
        if y < 100: # Nueva página si se llena
            c.showPage()
            y = height - 50
            
    # Paralizaciones
    if datos_para:
        y -= 20
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, f"⚠️ DELAY: {datos_para['motivo']}")
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 15, f"Time Lost: {datos_para['inicio']} to {datos_para['fin']} ({datos_para['duracion']}h)")
        c.setFillColor(colors.black)
        y -= 40
        
    # Producción Realizada
    if prod_dia:
        y -= 20
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Production / Works Done:")
        y -= 15
        c.setFont("Helvetica", 9)
        for item, acts in prod_dia.items():
            linea = f"• Item {item}: {', '.join(acts)}"
            c.drawString(60, y, linea)
            y -= 15
            
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
#           INTERFAZ DE USUARIO (GUI)
# ==========================================

# Variables de Sesión para mantener datos al recargar
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

# Pestañas Superiores
tab1, tab2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

# ------------------------------------------
#           PESTAÑA 1: PARTES
# ------------------------------------------
with tab1:
    st.subheader("Datos Generales")
    
    # 1. FILA DE FECHA Y VEHÍCULO
    c_f1, c_f2, c_f3, c_veh, c_info = st.columns([1, 1, 1, 2, 2])
    
    # Selectores de Fecha Independientes
    hoy = datetime.now()
    dia = c_f1.selectbox("Día", [i for i in range(1, 32)], index=hoy.day-1)
    mes = c_f2.selectbox("Mes", [i for i in range(1, 13)], index=hoy.month-1)
    ano = c_f3.selectbox("Año", [2024, 2025, 2026], index=1)
    
    try:
        fecha_sel = datetime(ano, mes, dia)
    except:
        st.error("Fecha inválida")
        fecha_sel = hoy

    # Carga de Vehículos (Diccionario)
    dicc_vehiculos = cargar_vehiculos_dict()
    if dicc_vehiculos:
        nombres_veh = list(dicc_vehiculos.keys())
        vehiculo_sel = c_veh.selectbox("Vehículo / Encargado", nombres_veh)
        # Info automática
        info_extra = dicc_vehiculos.get(vehiculo_sel, "")
        c_info.text_input("Detalle / Matrícula", value=info_extra, disabled=True)
    else:
        vehiculo_sel = c_veh.selectbox("Vehículo", ["Cargando..."])
        c_info.text_input("Detalle", disabled=True)
        
    st.divider()
    
    # 2. FILTRO DE PERSONAL (BOTONES RADIALES)
    st.write("**Filtrar Personal:**")
    filtro = st.radio("Filtro", ["TODOS", "OBRA", "ALMACEN"], horizontal=True, label_visibility="collapsed")
    
    # 3. AÑADIR OPERARIO
    c_add1, c_add2, c_add3, c_add4, c_add5 = st.columns([3, 1, 1, 1, 1])
    
    todos_trabajadores = cargar_trabajadores()
    
    # Lógica de filtrado
    if filtro == "ALMACEN":
        filtrados = [t for t in todos_trabajadores if t['tipo'] == "ALMACEN"]
        default_comida = True # Almacén marca comida por defecto
    elif filtro == "OBRA":
        filtrados = [t for t in todos_trabajadores if t['tipo'] != "ALMACEN"]
        default_comida = False
    else:
        filtrados = todos_trabajadores
        default_comida = False
        
    if not filtrados:
        opciones_nombres = ["Sin resultados"]
    else:
        opciones_nombres = [t['display'] for t in filtrados]
        
    trabajador_sel = c_add1.selectbox("Seleccionar Operario", opciones_nombres)
    
    # Horas por defecto
    h_ini_def = datetime.strptime("07:00", "%H:%M").time()
    h_fin_def = datetime.strptime("16:00", "%H:%M").time()
    
    h_ini = c_add2.time_input("Inicio", h_ini_def)
    h_fin = c_add3.time_input("Fin", h_fin_def)
    turno_manual = c_add4.selectbox("Turno", ["AUT", "D", "N"])
    desc_comida = c_add5.checkbox("-1h Comida", value=default_comida)
    
    # Botón Añadir
    if st.button("➕ AÑADIR A LA LISTA", use_container_width=True, type="secondary"):
        if trabajador_sel and trabajador_sel != "Sin resultados":
            # Cálculos de horas
            t_i = datetime.combine(fecha_sel, h_ini)
            t_f = datetime.combine(fecha_sel, h_fin)
            if t_f < t_i: t_f += timedelta(days=1) # Turno noche cruza día
            
            horas_totales = (t_f - t_i).total_seconds() / 3600
            
            # Auto-detección turno
            es_noche = False
            t_letra = "D"
            if turno_manual == "N" or (turno_manual=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)):
                es_noche, t_letra = True, "N"
            
            if desc_comida: horas_totales = max(0, horas_totales - 1)
            
            # Extraer ID y Nombre
            parts = trabajador_sel.split(" - ", 1)
            uid = parts[0]
            nombre = parts[1] if len(parts)>1 else uid
            
            st.session_state.lista_sel.append({
                "ID": uid, "Nombre": nombre, 
                "H_Inicio": h_ini.strftime("%H:%M"), "H_Fin": h_fin.strftime("%H:%M"),
                "Total_Horas": round(horas_totales, 2), 
                "Turno_Letra": t_letra, 
                "Es_Noche": es_noche
            })

    # 4. TABLA VISUAL DE LA CUADRILLA
    if st.session_state.lista_sel:
        st.markdown("### 📋 Cuadrilla del Día")
        df_show = pd.DataFrame(st.session_state.lista_sel)
        # Mostramos solo columnas útiles
        st.dataframe(df_show[["ID", "Nombre", "H_Inicio", "H_Fin", "Total_Horas", "Turno_Letra"]], use_container_width=True)
        
        if st.button("🗑️ Borrar toda la lista"):
            st.session_state.lista_sel = []
            st.rerun()

    st.divider()
    
    # 5. PARALIZACIONES
    tiene_para = st.checkbox("🛑 Registrar Paralización / Retraso")
    d_para = None
    if tiene_para:
        c_p1, c_p2, c_p3 = st.columns([1, 1, 2])
        hi_p = c_p1.time_input("Inicio Parada")
        hf_p = c_p2.time_input("Fin Parada")
        motivo_p = c_p3.text_input("Motivo / Causa")
        
        # Calcular duración parada
