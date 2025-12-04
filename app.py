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
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs EXACTOS DE TUS ARCHIVOS ---
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#           CONEXIÓN (GOOGLE SHEETS)
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

def conectar_por_id(file_id):
    client = get_gspread_client()
    try: return client.open_by_key(file_id)
    except: return None

def conectar_por_nombre(nombre_archivo):
    client = get_gspread_client()
    try: return client.open(nombre_archivo)
    except: return None

# ==========================================
#      ENVÍO POR EMAIL (GMAIL)
# ==========================================
def enviar_email_pdf(pdf_buffer, nombre_archivo, fecha_str, jefe):
    try:
        if "email" not in st.secrets: return False
        
        user = st.secrets["email"]["usuario"]
        pwd = st.secrets["email"]["password"]
        dest = st.secrets["email"]["destinatario"]

        msg = MIMEMultipart()
        msg['From'] = user
        msg['To'] = dest
        msg['Subject'] = f"📄 Parte: {fecha_str} - {jefe}"

        body = f"Adjunto parte de trabajo.\nFecha: {fecha_str}\nVehículo/Lugar: {jefe}"
        msg.attach(MIMEText(body, 'plain'))

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(pdf_buffer.getvalue())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= {nombre_archivo}")
        msg.attach(part)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(user, pwd)
        text = msg.as_string()
        server.sendmail(user, dest, text)
        server.quit()
        return True
    except: return False

# ==========================================
#      CARGA DE DATOS (CON CACHÉ)
# ==========================================
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
            if not nombre or nombre.lower() in ["nombre", "vehiculo", "vehicle", "nan"]: continue
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
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid = str(fila[0]).strip()
            nombre = str(fila[1]).strip()
            tipo = "OBRA"
            if len(fila) > 2:
                marca = str(fila[2]).strip().upper()
                if marca == "A" or "ALMACEN" in marca: tipo = "ALMACEN"
            if uid and nombre and uid.lower() != "id":
                lista_trabajadores.append({"display": f"{uid} - {nombre}", "tipo": tipo, "id": uid, "nombre_solo": nombre})
        return lista_trabajadores
    except: return []

@st.cache_data(ttl=600)
def cargar_config_prod():
    sh = conectar_por_id(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            if len(row) >= 2 and row[0] and row[1]: config[row[0].strip()] = row[1].strip()
        return config
    except: return {}

def buscar_columna_dia(ws, dia_num):
    header_rows = ws.get_values("E4:AX9") 
    for r_idx, row in enumerate(header_rows):
        for c_idx, val in enumerate(row):
            if val and (str(val).strip() == str(dia_num)): return c_idx + 5 
    dias_dif = int(dia_num) - 21
    if dias_dif < 0: dias_dif += 30
    return 14 + (dias_dif * 2)

# ==========================================
#          GUARDADO EXCEL
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
            ws_para.append_row([str(fecha_dt.date()), vehiculo, datos_paralizacion['inicio'], datos_paralizacion['fin'], datos_paralizacion['duracion'], datos_paralizacion['motivo']])
        return True
    except: return False

def guardar_produccion(archivo_prod, hoja_prod, fila, col, valor):
    sh = conectar_por_nombre(archivo_prod)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja_prod)
        ws.update_cell(fila, col, valor)
        return True
    except: return False

# ==========================================
#          GENERADOR PDF (DISEÑO FINAL)
# ==========================================
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    
    # --- LÓGICA HORAS GLOBALES (PRIMER TRABAJADOR) ---
    start_time = "________"
    end_time = "________"
    
    if trabajadores:
        # El primero de la lista manda
        primero = trabajadores[0]
        start_time = primero['H_Inicio']
        end_time = primero['H_Fin']

    # --- CABECERA (DISEÑO CAJA) ---
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10)
    c.drawString(400, height - 50, "Israel Railways Project")
    
    # Recuadro Cabecera
    y_header = height - 90
    c.rect(40, y_header - 60, 515, 70) 
    
    c.setFont("Helvetica-Bold", 10)
    # Fila 1
    c.drawString(50, y_header - 15, f"Date: {fecha_str}")
    c.drawString(250, y_header - 15, f"Vehículo/Lugar: {jefe}") # CAMBIO SOLICITADO
    
    # Fila 2 (Horas del primer trabajador)
    c.drawString(50, y_header - 45, f"Start Time: {start_time}") # CAMBIO SOLICITADO
    c.drawString(200, y_header - 45, f"End Time: {end_time}")   # CAMBIO SOLICITADO
    c.drawString(350, y_header - 45, "Weather: ________")
    
    # --- TABLA TRABAJADORES ---
    y = y_header - 80
    
    # Barra Azul Títulos
    c.setFillColor(colors.HexColor("#2980B9"))
    c.rect(40, y, 515, 20, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    
    headers = ["ID", "Name", "In", "Out", "Hours", "Shift"]
    x_coords = [50, 100, 300, 350, 400, 460]
    
    c.drawString(x_coords[0], y+6, "ID")
    c.drawString(x_coords[1], y+6, "Employee Name")
    c.drawString(x_coords[2], y+6, "In")
    c.drawString(x_coords[3], y+6, "Out")
    c.drawString(x_coords[4], y+6, "Total")
    c.drawString(x_coords[5], y+6, "Shift")
    
    y -= 20
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    
    for t in trabajadores:
        c.drawString(x_coords[0], y+6, str(t['ID']))
        c.drawString(x_coords[1], y+6, t['Nombre'][:35])
        c.drawString(x_coords[2], y+6, t['H_Inicio'])
        c.drawString(x_coords[3], y+6, t['H_Fin'])
        c.drawString(x_coords[4], y+6, str(t['Total_Horas']))
        c.drawString(x_coords[5], y+6, t['Turno_Letra'])
        
        c.setLineWidth(0.5)
        c.line(40, y, 555, y) # Línea horizontal
        y -= 20
        
        if y < 100: c.showPage(); y = height - 50
            
    # --- PARALIZACIONES ---
    if datos_para:
        y -= 30
        c.setStrokeColor(colors.red)
        c.rect(40, y - 40, 515, 50)
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y - 15, "⚠️ PARALIZACIÓN / CLIENT DELAY")
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 10)
        c.drawString(50, y - 35, f"Time: {datos_para['inicio']} - {datos_para['fin']} ({datos_para['duracion']}h) | Reason: {datos_para['motivo']}")
        c.setStrokeColor(colors.black)
        y -= 60
        
    # --- PRODUCCIÓN ---
    if prod_dia:
        y -= 20
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Production / Works Done:")
        y -= 20
        c.setFont("Helvetica", 9)
        for k, v in prod_dia.items():
            c.drawString(60, y, f"• {k}: {', '.join(v)}")
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
        
    opciones_nombres = [""] + [t['display'] for t in filtrados] if filtrados else ["Sin resultados"]
    trabajador_sel = c_add1.selectbox("Seleccionar Operario", opciones_nombres)
    
    h_ini = c_add2.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
    h_fin = c_add3.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
    turno_manual = c_add4.selectbox("Turno", ["AUT", "D", "N"])
    desc_comida = c_add5.checkbox("-1h Comida", value=default_comida)
    
    if st.button("➕ AÑADIR A LA LISTA", use_container_width=True, type="secondary"):
        if trabajador_sel and trabajador_sel != "Sin resultados" and trabajador_sel != "":
            t_i = datetime.combine(fecha_sel, h_ini)
            t_f = datetime.combine(fecha_sel, h_fin)
            if t_f < t_i: t_f += timedelta(days=1)
            horas = (t_f - t_i).total_seconds() / 3600
            
            es_noche, t_letra = False, "D"
            cond_manual_noche = (turno_manual == "N")
            cond_auto_noche = False
            if turno_manual == "AUT" and (h_ini.hour >= 21 or h_ini.hour <= 4): cond_auto_noche = True
            if cond_manual_noche or cond_auto_noche: es_noche, t_letra = True, "N"
            
            if desc_comida: horas = max(0, horas - 1)
            parts = trabajador_sel.split(" - ", 1)
            st.session_state.lista_sel.append({
                "ID": parts[0], "Nombre": parts[1] if len(parts)>1 else parts[0], 
                "H_Inicio": h_ini.strftime("%H:%M"), "H_Fin": h_fin.strftime("%H:%M"),
                "Total_Horas": round(horas, 2), "Turno_Letra": t_letra, "Es_Noche": es_noche
            })
        else: st.warning("Selecciona un operario.")

    if st.session_state.lista_sel:
        st.markdown("### 📋 Cuadrilla del Día")
        df_show = pd.DataFrame(st.session_state.lista_sel)
        st.dataframe(df_show[["ID", "Nombre", "H_Inicio", "H_Fin", "Total_Horas", "Turno_Letra"]], use_container_width=True)
        if st.button("🗑️ Borrar lista"): st.session_state.lista_sel = []; st.rerun()

    st.divider()
    tiene_para = st.checkbox("🛑 Registrar Paralización")
    d_para = None
    if tiene_para:
        c_p1, c_p2, c_p3 = st.columns([1, 1, 2])
        hi_p = c_p1.time_input("Inicio Parada")
        hf_p = c_p2.time_input("Fin Parada")
        motivo_p = c_p3.text_input("Motivo")
        d1, d2 = datetime.combine(datetime.today(), hi_p), datetime.combine(datetime.today(), hf_p)
        dur_p = round((d2 - d1).total_seconds() / 3600, 2)
        d_para = {"inicio": str(hi_p), "fin": str(hf_p), "duracion": max(0, dur_p), "motivo": motivo_p}

    if st.button("💾 GUARDAR TODO (Excel + Email)", type="primary", use_container_width=True):
        if not st.session_state.lista_sel: st.error("Lista vacía.")
        elif not vehiculo_sel: st.error("Falta seleccionar vehículo.")
        else:
            with st.spinner("Guardando en la Nube y Enviando Email..."):
                ok_datos = guardar_parte_en_nube(fecha_sel, st.session_state.lista_sel, vehiculo_sel, d_para)
                pdf_bytes = generar_pdf_bytes(str(fecha_sel.date()), vehiculo_sel, st.session_state.lista_sel, d_para, st.session_state.prod_dia)
                nombre_pdf = f"Parte_{fecha_sel.strftime('%Y-%m-%d')}_{vehiculo_sel}.pdf"
                
                enviado = enviar_email_pdf(pdf_bytes, nombre_pdf, str(fecha_sel.date()), vehiculo_sel)
                msg_email = "📧 Email enviado." if enviado else "⚠️ Fallo al enviar email (Revisa contraseña)."

                if ok_datos:
                    st.success(f"✅ ¡Datos guardados! {msg_email}")
                    st.download_button("📥 Descargar Copia en Tablet", pdf_bytes, nombre_pdf, "application/pdf")
                    st.session_state.lista_sel = []; st.session_state.prod_dia = {}; time.sleep(5); st.rerun()

# ---------------- PESTAÑA 2 ----------------
with tab2:
    st.header("🏗️ Control de Producción")
    config_prod = cargar_config_prod()
    if not config_prod: st.warning("Configuración no encontrada.")
    else:
        tramo_sel = st.selectbox("Seleccionar Tramo", list(config_prod.keys()))
        archivo_prod = config_prod.get(tramo_sel)
        if archivo_prod:
            sh_prod = conectar_por_nombre(archivo_prod)
            if sh_prod:
                hojas = [ws.title for ws in sh_prod.worksheets() if "HR TRACK" in ws.title.upper()]
                hoja_sel = st.selectbox("Hoja de Seguimiento", hojas) if hojas else None
                if hoja_sel:
                    ws_prod = sh_prod.worksheet(hoja_sel)
                    col_a = ws_prod.col_values(1)
                    items = [x for x in col_a if x and len(x)>2 and x.upper() not in ["ITEM","HR TRACK","TOTAL"]]
                    filtro_km = st.text_input("🔍 Filtro Rápido (Km):")
                    if filtro_km: items = [i for i in items if filtro_km in i]
                    item_sel = st.selectbox("Elemento", items)
                    if item_sel:
                        fila = col_a.index(item_sel) + 1
                        st.markdown(f"### {item_sel}")
                        c1, c2 = st.columns(2)
                        ec, fc = ws_prod.cell(fila, 3).value, ws_prod.cell(fila, 5).value
                        c1.metric("Cimentación", str(ec) if ec else "---")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("✅ Marcar CIM"):
                            guardar_produccion(archivo_prod, hoja_sel, fila, 5, datetime.now().strftime("%d/%m/%Y"))
                            if item_sel not in st.session_state.prod_dia: st.session_state.prod_dia[item_sel] = []
                            st.session_state.prod_dia[item_sel].append("CIM")
                            st.rerun()
                        st.divider()
                        c1, c2 = st.columns(2)
                        ep, fp = ws_prod.cell(fila, 6).value, ws_prod.cell(fila, 8).value
                        c1.metric("Poste", str(ep) if ep else "---")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("✅ Marcar POSTE"):
                            guardar_produccion(archivo_prod, hoja_sel, fila, 8, datetime.now().strftime("%d/%m/%Y"))
                            if item_sel not in st.session_state.prod_dia: st.session_state.prod_dia[item_sel] = []
                            st.session_state.prod_dia[item_sel].append("POSTE")
                            st.rerun()

