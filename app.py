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

# --- NOMBRES DE ARCHIVOS EN GOOGLE DRIVE (Deben coincidir EXACTAMENTE) ---
FILE_ROSTER = "Roster 2025 12 (empty)"
FILE_VEHICULOS = "Vehiculos 2"
FILE_CONFIG_PROD = "ARCHIVOS DE PRODUCION"

# --- CONEXIÓN CON GOOGLE SHEETS ---
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
        st.error(f"❌ No se encuentra el archivo: '{nombre_archivo}'. Asegúrate de que está en Drive y compartido con el robot.")
        return None

# ==========================================
#      LOGICA PESTAÑA 1: PARTES DE TRABAJO
# ==========================================

def cargar_vehiculos():
    sh = conectar_hoja(FILE_VEHICULOS)
    if not sh: return []
    try:
        # Leemos la primera hoja
        ws = sh.sheet1
        datos = ws.get_all_values()
        lista = []
        for fila in datos:
            if not fila: continue
            nombre = fila[0].strip()
            if nombre.lower() not in ["nombre", "vehiculo", "vehicle", "nan", ""]:
                lista.append(nombre)
        return lista
    except:
        return []

def cargar_trabajadores():
    sh = conectar_hoja(FILE_ROSTER)
    if not sh: return []
    try:
        # Buscamos la hoja 'Roster' o usamos la primera
        try:
            ws = sh.worksheet("Roster")
        except:
            ws = sh.sheet1
            
        # Asumimos que los datos empiezan en fila 9 como en tu código original
        # Leemos columna A (ID) y B (Nombre)
        col_id = ws.col_values(1)[8:] # Saltamos 8 filas de cabecera
        col_nom = ws.col_values(2)[8:]
        
        lista_trabajadores = []
        for i in range(len(col_nom)):
            if i < len(col_id):
                uid = str(col_id[i]).strip()
                nombre = str(col_nom[i]).strip()
                if uid and nombre and uid.lower() != "id":
                    lista_trabajadores.append(f"{uid} - {nombre}")
        return lista_trabajadores
    except Exception as e:
        st.error(f"Error leyendo trabajadores: {e}")
        return []

def calcular_horas_auto(inicio, fin, turno_manual):
    fmt = "%H:%M"
    try:
        t_ini = datetime.strptime(inicio, fmt)
        t_fin = datetime.strptime(fin, fmt)
        if t_fin < t_ini: t_fin += timedelta(days=1)
        duracion = (t_fin - t_ini).total_seconds() / 3600
        
        es_noche = False
        turno_letra = "D"
        
        if turno_manual == "N": 
            es_noche, turno_letra = True, "N"
        elif turno_manual == "D": 
            es_noche, turno_letra = False, "D"
        else: # Auto
            if t_ini.hour >= 21 or t_ini.hour <= 4:
                es_noche, turno_letra = True, "N"
            else:
                turno_letra = "D"
                
        return round(duracion, 2), turno_letra, es_noche
    except:
        return 0.0, "Err", False

def buscar_columna_dia(ws, dia_num):
    # Lógica de búsqueda de tu código original adaptada a gspread
    # Buscamos en las filas 4 a 8 (indices 3 a 7)
    # gspread usa indices base-1
    header_rows = ws.get_values("E4:AX9") # Rango amplio de cabecera
    
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            try:
                if val and (val == dia_num or str(val).strip() == str(dia_num)):
                    # c_idx es indice en la lista, sumamos 5 porque empezamos en col E (5)
                    return c_idx + 5 
            except: pass
    
    # Si no encuentra, usamos la fórmula de emergencia de tu código
    # col_dia = 14 + (dias_diferencia * 2)
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

def guardar_parte_en_nube(fecha_dt, lista_trabajadores, vehiculo, datos_paralizacion):
    sh = conectar_hoja(FILE_ROSTER)
    if not sh: return False
    
    try:
        # 1. ACTUALIZAR ROSTER (HORAS)
        try:
            ws = sh.worksheet("Roster")
        except:
            ws = sh.sheet1
            
        dia_num = fecha_dt.day
        col_dia = buscar_columna_dia(ws, dia_num)
        
        # Leemos columna ID para buscar filas rápido
        col_ids = ws.col_values(1)
        
        cells_to_update = []
        
        for t in lista_trabajadores:
            id_buscado = t['ID']
            try:
                # Busamos la fila del trabajador
                fila = col_ids.index(id_buscado) + 1 # +1 porque index es base-0
                
                # Turno en col_dia, Horas en col_dia + 1
                cells_to_update.append(gspread.Cell(fila, col_dia, t['Turno_Letra']))
                cells_to_update.append(gspread.Cell(fila, col_dia + 1, t['Total_Horas']))
                
            except ValueError:
                st.warning(f"Trabajador {id_buscado} no encontrado en Roster.")
                
        if cells_to_update:
            ws.update_cells(cells_to_update)
            st.toast(f"✅ Roster actualizado para {len(lista_trabajadores)} operarios.")

        # 2. REGISTRAR PARALIZACIÓN (Si hay)
        if datos_paralizacion:
            try:
                ws_para = sh.worksheet("Paralizaciones")
            except:
                ws_para = sh.add_worksheet("Paralizaciones", 1000, 10)
                ws_para.append_row(["Fecha", "Vehiculo/Encargado", "Hora Inicio", "Hora Fin", "Horas Perdidas", "Motivo"])
            
            fila_para = [
                str(fecha_dt.date()), 
                vehiculo, 
                datos_paralizacion['inicio'], 
                datos_paralizacion['fin'], 
                datos_paralizacion['duracion'], 
                datos_paralizacion['motivo']
            ]
            ws_para.append_row(fila_para)
            st.toast("⚠️ Paralización registrada.")
            
        return True

    except Exception as e:
        st.error(f"Error guardando: {e}")
        return False

# --- GENERADOR DE PDF (En memoria) ---
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # ... (Lógica de dibujo idéntica a tu código, simplificada para brevedad) ...
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10)
    c.drawString(400, height - 50, "Israel Railways Project")
    
    y = height - 90
    c.rect(40, y - 60, 515, 70)
    c.drawString(50, y - 15, f"Date: {fecha_str}")
    c.drawString(250, y - 15, f"Vehicle: {jefe}")
    
    # Tabla trabajadores
    y_cursor = y - 80
    c.setFont("Helvetica-Bold", 8)
    headers = ["ID", "Name", "In", "Out", "Hours", "Shift"]
    x_pos = [40, 90, 250, 300, 350, 400]
    for i, h in enumerate(headers): c.drawString(x_pos[i], y_cursor, h)
    y_cursor -= 15
    c.setFont("Helvetica", 9)
    
    for t in trabajadores:
        c.drawString(x_pos[0], y_cursor, str(t['ID']))
        c.drawString(x_pos[1], y_cursor, t['Nombre'][:25])
        c.drawString(x_pos[2], y_cursor, t['H_Inicio'])
        c.drawString(x_pos[3], y_cursor, t['H_Fin'])
        c.drawString(x_pos[4], y_cursor, str(t['Total_Horas']))
        c.drawString(x_pos[5], y_cursor, t['Turno_Letra'])
        y_cursor -= 15
        
    # Paralización
    if datos_para:
        y_cursor -= 20
        c.setFillColor(colors.red)
        c.drawString(50, y_cursor, f"⚠️ DELAY: {datos_para['inicio']} - {datos_para['fin']} ({datos_para['motivo']})")
        c.setFillColor(colors.black)
        
    # Producción
    if prod_dia:
        y_cursor -= 30
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y_cursor, "Production / Works:")
        y_cursor -= 15
        c.setFont("Helvetica", 9)
        for item, acts in prod_dia.items():
            c.drawString(50, y_cursor, f"- {item}: {', '.join(acts)}")
            y_cursor -= 12
            
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
#      LOGICA PESTAÑA 2: PRODUCCIÓN
# ==========================================

def cargar_config_prod():
    sh = conectar_hoja(FILE_CONFIG_PROD)
    if not sh: return {}
    datos = sh.sheet1.get_all_values()
    config = {}
    for row in datos:
        if len(row) >= 2 and row[0] and row[1]:
            config[row[0].strip()] = row[1].strip() # Tramo -> Nombre Archivo
    return config

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
#               INTERFAZ GRÁFICA
# ==========================================

tab1, tab2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

# --- VARIABLES DE SESIÓN ---
if 'lista_trabajadores_sel' not in st.session_state: st.session_state.lista_trabajadores_sel = []
if 'prod_del_dia' not in st.session_state: st.session_state.prod_del_dia = {}

# ---------------- PESTAÑA 1 ----------------
with tab1:
    st.header("Gestión de Personal")
    
    col1, col2, col3 = st.columns(3)
    fecha_sel = col1.date_input("Fecha", datetime.now())
    vehiculos = cargar_vehiculos()
    vehiculo_sel = col2.selectbox("Vehículo / Encargado", vehiculos if vehiculos else ["Cargando..."])
    
    st.divider()
    
    # Añadir Trabajador
    c_add1, c_add2, c_add3, c_add4, c_add5 = st.columns([3, 1, 1, 1, 1])
    todos_trabajadores = cargar_trabajadores()
    trabajador_sel = c_add1.selectbox("Seleccionar Operario", todos_trabajadores)
    
    h_ini = c_add2.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
    h_fin = c_add3.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
    turno_manual = c_add4.selectbox("Turno", ["AUT", "D", "N"])
    desc_comida = c_add5.checkbox("-1h Comida")
    
    if st.button("➕ Añadir Operario", use_container_width=True):
        if trabajador_sel:
            str_ini = h_ini.strftime("%H:%M")
            str_fin = h_fin.strftime("%H:%M")
            horas, turno, es_noche = calcular_horas_auto(str_ini, str_fin, turno_manual)
            if desc_comida: horas = max(0, horas - 1)
            
            uid, nombre = trabajador_sel.split(" - ", 1)
            
            st.session_state.lista_trabajadores_sel.append({
                "ID": uid, "Nombre": nombre, 
                "H_Inicio": str_ini, "H_Fin": str_fin, 
                "Total_Horas": horas, "Turno_Letra": turno, "Es_Noche": es_noche
            })
            st.success(f"Añadido: {nombre}")

    # Tabla de seleccionados
    if st.session_state.lista_trabajadores_sel:
        df_sel = pd.DataFrame(st.session_state.lista_trabajadores_sel)
        st.dataframe(df_sel, use_container_width=True)
        
        if st.button("🗑️ Borrar Lista"):
            st.session_state.lista_trabajadores_sel = []
            st.rerun()

    st.divider()
    
    # Paralización
    tiene_para = st.checkbox("Registrar Paralización / Retraso")
    datos_para = None
    if tiene_para:
        cp1, cp2, cp3 = st.columns([1,1,3])
        p_ini = cp1.time_input("Inicio Parada")
        p_fin = cp2.time_input("Fin Parada")
        p_motivo = cp3.text_input("Motivo")
        
        # Calculamos duración
        dummy_date = datetime.today()
        d_ini = datetime.combine(dummy_date, p_ini)
        d_fin = datetime.combine(dummy_date, p_fin)
        dur_para = round((d_fin - d_ini).total_seconds() / 3600, 2)
        if dur_para < 0: dur_para = 0
        
        datos_para = {
            "inicio": p_ini.strftime("%H:%M"), "fin": p_fin.strftime("%H:%M"),
            "duracion": dur_para, "motivo": p_motivo
        }

    # BOTÓN FINAL GUARDAR
    if st.button("💾 GUARDAR TODO Y GENERAR PARTE", type="primary", use_container_width=True):
        if not st.session_state.lista_trabajadores_sel:
            st.error("Lista de trabajadores vacía.")
        else:
            with st.spinner("Guardando en Google Sheets..."):
                exito = guardar_parte_en_nube(fecha_sel, st.session_state.lista_trabajadores_sel, vehiculo_sel, datos_para)
                
                if exito:
                    # Generar PDF
                    pdf_bytes = generar_pdf_bytes(str(fecha_sel), vehiculo_sel, st.session_state.lista_trabajadores_sel, datos_para, st.session_state.prod_del_dia)
                    
                    st.success("✅ ¡Datos guardados correctamente en la nube!")
                    
                    # Botón descarga PDF
                    st.download_button(
                        label="📥 Descargar PDF del Parte",
                        data=pdf_bytes,
                        file_name=f"Parte_{fecha_sel}_{vehiculo_sel}.pdf",
                        mime="application/pdf"
                    )
                    
                    # Limpiar
                    st.session_state.lista_trabajadores_sel = []
                    st.session_state.prod_del_dia = {}

# ---------------- PESTAÑA 2 ----------------
with tab2:
    st.header("Control de Producción")
    
    # 1. Selección de Tramo
    config_prod = cargar_config_prod()
    tramos = list(config_prod.keys())
    tramo_sel = st.selectbox("Seleccionar Tramo / Proyecto", tramos)
    
    archivo_prod_nombre = config_prod.get(tramo_sel)
    
    if archivo_prod_nombre:
        st.info(f"Conectado a: {archivo_prod_nombre}")
        sh_prod = conectar_hoja(archivo_prod_nombre)
        
        if sh_prod:
            # 2. Selección de Hoja
            hojas = [ws.title for ws in sh_prod.worksheets() if "HR TRACK" in ws.title.upper()]
            hoja_sel = st.selectbox("Seleccionar Hoja (HR TRACK)", hojas)
            
            if hoja_sel:
                ws_prod = sh_prod.worksheet(hoja_sel)
                
                # 3. Cargar Items (Columna A)
                # Cacheamos esto un poco para que no sea lento
                col_a = ws_prod.col_values(1)
                items = [x for x in col_a if x and x.upper() not in ["HR TRACK", "NAN", "ITEM", "TOTAL"]]
                
                # Filtro rápido
                filtro_km = st.text_input("Filtrar por Km (ej: 52):")
                items_filtrados = [i for i in items if filtro_km in i] if filtro_km else items
                
                item_sel = st.selectbox("Seleccionar Elemento", items_filtrados)
                
                if item_sel:
                    # Buscamos la fila del item
                    fila_item = col_a.index(item_sel) + 1
                    
                    st.subheader(f"Detalles de: {item_sel}")
                    
                    # Leemos estado actual (Usando cell para no leer toda la hoja)
                    # Cimentación: Col C (3) Estado, Col E (5) Fecha
                    # Poste: Col F (6) Estado, Col H (8) Fecha
                    
                    # --- CIMENTACIÓN ---
                    c_cim1, c_cim2 = st.columns(2)
                    estado_cim = ws_prod.cell(fila_item, 3).value
                    fecha_cim = ws_prod.cell(fila_item, 5).value
                    
                    c_cim1.metric("Cimentación", str(estado_cim) if estado_cim else "---")
                    
                    if fecha_cim:
                        c_cim2.success(f"Realizado: {fecha_cim}")
                    else:
                        if c_cim2.button("✅ Marcar CIM como Realizada"):
                            hoy = datetime.now().strftime("%d/%m/%Y")
                            guardar_produccion(archivo_prod_nombre, hoja_sel, fila_item, 5, hoy)
                            # Añadir a reporte
                            if item_sel not in st.session_state.prod_del_dia: st.session_state.prod_del_dia[item_sel] = []
                            st.session_state.prod_del_dia[item_sel].append(f"CIM ({estado_cim})")
                            st.rerun()

                    st.divider()

                    # --- POSTE ---
                    c_pos1, c_pos2 = st.columns(2)
                    estado_poste = ws_prod.cell(fila_item, 6).value
                    fecha_poste = ws_prod.cell(fila_item, 8).value
                    
                    c_pos1.metric("Poste", str(estado_poste) if estado_poste else "---")
                    
                    if fecha_poste:
                        c_pos2.success(f"Realizado: {fecha_poste}")
                    else:
                        if c_pos2.button("✅ Marcar POSTE como Realizado"):
                            hoy = datetime.now().strftime("%d/%m/%Y")
                            guardar_produccion(archivo_prod_nombre, hoja_sel, fila_item, 8, hoy)
                            if item_sel not in st.session_state.prod_del_dia: st.session_state.prod_del_dia[item_sel] = []
                            st.session_state.prod_del_dia[item_sel].append(f"POSTE ({estado_poste})")
                            st.rerun()
                            
                    st.divider()
                    
                    # --- MENSULAS Y ANCLAJES ---
                    st.info("Para Ménsulas y Anclajes, la lógica es similar. Se expandirá aquí.")
                    # (Aquí iría la lógica repetida para las columnas de Ménsulas y Anclajes que tenías)
