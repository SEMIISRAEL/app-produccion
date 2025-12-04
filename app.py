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

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs FIJOS ---
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#           SISTEMA DE LOGIN
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
                    else: st.error("❌ Datos incorrectos")
                except: st.error("⚠️ Error en Secrets.")
        return False
    return True

if not check_login(): st.stop()

# ==========================================
#      SIDEBAR (CONFIGURACIÓN)
# ==========================================
@st.cache_data(ttl=300)
def buscar_archivos_roster():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        scopes = ['https://www.googleapis.com/auth/drive']
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
        service = build('drive', 'v3', credentials=creds)
        query = "name contains 'Roster' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)", orderBy="name desc").execute()
        items = results.get('files', [])
        return {f['name']: f['id'] for f in items}
    except: return {}

with st.sidebar:
    st.write(f"👤 **{st.session_state.user_name.upper()}** ({st.session_state.user_role})")
    if st.button("Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()
    st.markdown("---")
    
    archivos_roster = buscar_archivos_roster()
    ID_ROSTER_ACTIVO = None
    if archivos_roster:
        if st.session_state.user_role == "admin":
            st.header("🗂️ Configuración")
            nombre_roster_sel = st.selectbox("Archivo Horas:", list(archivos_roster.keys()))
            ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
            st.success(f"Editando: {nombre_roster_sel}")
        else:
            nombre_roster_sel = list(archivos_roster.keys())[0]
            ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
    else: st.error("No hay Rosters.")

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

def conectar_por_id(file_id):
    client = get_gspread_client()
    try: return client.open_by_key(file_id)
    except: return None

# ESTA FUNCIÓN NO TIENE CACHÉ PARA PODER REINTENTAR SIEMPRE
def conectar_por_nombre_raw(nombre_archivo):
    client = get_gspread_client()
    try:
        return client.open(nombre_archivo)
    except:
        # Intento inteligente: si falla, prueba añadiendo o quitando .xlsx
        try:
            if ".xlsx" in nombre_archivo:
                return client.open(nombre_archivo.replace(".xlsx", "").strip())
            else:
                return client.open(nombre_archivo + ".xlsx")
        except:
            return None

# ESTA SÍ TIENE CACHÉ PARA LA LISTA DE HOJAS (SOLUCIÓN AL ERROR 429)
@st.cache_data(ttl=600, show_spinner=False)
def obtener_hojas_track_cached(nombre_archivo):
    sh = conectar_por_nombre_raw(nombre_archivo)
    if not sh: return None
    try:
        return [ws.title for ws in sh.worksheets() if "HR TRACK" in ws.title.upper()]
    except: return []

# ==========================================
#      EMAIL & CARGA DE DATOS
# ==========================================
def enviar_email_pdf(pdf_buffer, nombre_archivo, fecha_str, jefe):
    try:
        if "email" not in st.secrets: return False
        user = st.secrets["email"]["usuario"]
        pwd = st.secrets["email"]["password"]
        dest = st.secrets["email"]["destinatario"]
        
        msg = MIMEMultipart()
        msg['From'] = user; msg['To'] = dest; msg['Subject'] = f"📄 Parte: {fecha_str} - {jefe}"
        body = f"Fecha: {fecha_str}\nVehículo/Lugar: {jefe}\nUsuario: {st.session_state.user_name}"
        msg.attach(MIMEText(body, 'plain'))
        
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(pdf_buffer.getvalue())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= {nombre_archivo}")
        msg.attach(part)
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls(); server.login(user, pwd); server.sendmail(user, dest, msg.as_string()); server.quit()
        return True
    except: return False

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

def cargar_trabajadores_disponibles(fecha_dt, id_roster):
    if not id_roster: return []
    sh = conectar_por_id(id_roster)
    if not sh: return []
    try:
        try: ws = sh.worksheet("Roster")
        except: ws = sh.sheet1
        col_dia = buscar_columna_dia(ws, fecha_dt.day)
        datos = ws.get_all_values()
        lista_trabajadores = []
        idx_dia = col_dia - 1
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid = str(fila[0]).strip()
            nombre = str(fila[1]).strip()
            registrado = False
            if len(fila) > idx_dia:
                val = str(fila[idx_dia]).strip()
                if val and val not in ["", "None"]: registrado = True
            if registrado: continue 
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
def guardar_parte_en_nube(fecha_dt, lista_trabajadores, vehiculo, datos_paralizacion, id_roster):
    sh = conectar_por_id(id_roster)
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
                ws_para.append_row(["Fecha", "Vehiculo/Lugar", "Inicio", "Fin", "Horas", "Motivo", "Usuario"])
            ws_para.append_row([str(fecha_dt.date()), vehiculo, datos_paralizacion['inicio'], datos_paralizacion['fin'], datos_paralizacion['duracion'], datos_paralizacion['motivo'], st.session_state.user_name])
        return True
    except: return False

def guardar_produccion(archivo_prod, hoja_prod, fila, col, valor):
    sh = conectar_por_nombre_raw(archivo_prod)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja_prod)
        ws.update_cell(fila, col, valor)
        return True
    except: return False

# ==========================================
#      GENERADOR PDF (DISEÑO PROFESIONAL)
# ==========================================
def generar_pdf_bytes(fecha_str, jefe, trabajadores, datos_para, prod_dia):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    start_time, end_time = "________", "________"
    if trabajadores:
        start_time = trabajadores[0]['H_Inicio']
        end_time = trabajadores[0]['H_Fin']

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
    for i, h in enumerate(headers): c.drawString(x_coords[i] + 5, y_cursor + 6, h)
    
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
        c.setLineWidth(0.5); c.line(40, y_cursor, 555, y_cursor); y_cursor -= 20
        if y_cursor < 200: c.showPage(); y_cursor = height - 50
    
    y_min = height - 400
    while y_cursor > y_min: c.setLineWidth(0.5); c.line(40, y_cursor, 555, y_cursor); y_cursor -= 20
    
    c.setLineWidth(1)
    for x in x_coords: c.line(x, y_tabla_start + 20, x, y_final - 20)
    c.line(555, y_tabla_start + 20, 555, y_final - 20) 

    y_bloque = y_final - 40
    if datos_para:
        c.setStrokeColor(colors.red); c.setLineWidth(2); c.rect(40, y_bloque - 50, 515, 50)
        c.setFillColor(colors.red); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_bloque - 15, "⚠️ CLIENT DELAY / PARALIZACIÓN")
        c.setFillColor(colors.black); c.setFont("Helvetica", 10); c.drawString(50, y_bloque - 35, f"Time: {datos_para['inicio']} - {datos_para['fin']} ({datos_para['duracion']}h) | Reason: {datos_para['motivo']}")
        c.setStrokeColor(colors.black); c.setLineWidth(1); y_bloque -= 70

    y_act_top = y_bloque; alt_act = y_act_top - 130
    if alt_act > 20:
        c.rect(40, 130, 515, alt_act); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_act_top - 15, "Work Description / Location:")
        y_line = y_act_top - 35
        if prod_dia:
            c.setFont("Helvetica", 9)
            for k, v in prod_dia.items():
                c.drawString(50, y_line + 5, f"- {k}: {', '.join(v)}"); c.setLineWidth(0.5); c.line(40, y_line, 555, y_line); y_line -= 20
        while y_line > 135: c.setLineWidth(0.5); c.line(40, y_line, 555, y_line); y_line -= 20

    c.setLineWidth(1); c.rect(40, 30, 515, 90); c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 100, "Machinery / Materials:"); c.line(40, 70, 555, 70)
    c.drawString(50, 50, "SIGNATURE (ENCARGADO): __________________________")
    c.save(); buffer.seek(0); return buffer

# ==========================================
#           INTERFAZ DE USUARIO
# ==========================================
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

tab1, tab2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

# ---------------- PESTAÑA 1 ----------------
with tab1:
    if not ID_ROSTER_ACTIVO: st.warning("⚠️ No hay archivo Roster.")
    else:
        st.subheader("Datos Generales")
        c1, c2, c3, c4, c5 = st.columns([1,1,1,2,2])
        hoy = datetime.now()
        dia = c1.selectbox("Día", range(1,32), index=hoy.day-1)
        mes = c2.selectbox("Mes", range(1,13), index=hoy.month-1)
        ano = c3.selectbox("Año", [2024, 2025, 2026], index=1)
        try: fecha_sel = datetime(ano, mes, dia)
        except: fecha_sel = hoy; st.error("Fecha incorrecta")

        dicc_vehiculos = cargar_vehiculos_dict()
        if dicc_vehiculos:
            nombres_veh = [""] + list(dicc_vehiculos.keys())
            vehiculo_sel = c4.selectbox("Vehículo / Lugar", nombres_veh)
            info_extra = dicc_vehiculos.get(vehiculo_sel, "")
            c5.text_input("Detalle", value=info_extra, disabled=True)
        else:
            vehiculo_sel = c4.selectbox("Vehículo / Lugar", ["Error Carga"])
            c5.text_input("Detalle", disabled=True)
            
        st.divider()
        filtro = st.radio("Filtro:", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        c_a1, c_a2, c_a3, c_a4, c_a5 = st.columns([3,1,1,1,1])
        
        with st.spinner("Actualizando personal..."):
            all_trab = cargar_trabajadores_disponibles(fecha_sel, ID_ROSTER_ACTIVO)
            
        if filtro == "ALMACEN": 
            fil = [t for t in all_trab if t['tipo']=="ALMACEN"]; def_com=True
        elif filtro == "OBRA": 
            fil = [t for t in all_trab if t['tipo']!="ALMACEN"]; def_com=False
        else: 
            fil = all_trab; def_com=False
            
        opc = [""] + [t['display'] for t in fil] if fil else ["Sin personal disponible"]
        trab_sel = c_a1.selectbox("Operario", opc)
        
        h_ini = c_a2.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = c_a3.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        turno = c_a4.selectbox("Turno", ["AUT", "D", "N"])
        comida = c_a5.checkbox("-1h Comida", value=def_com)
        
        if st.button("➕ AÑADIR", type="secondary", use_container_width=True):
            if trab_sel and trab_sel not in ["", "Sin personal disponible"]:
                t1 = datetime.combine(fecha_sel, h_ini); t2 = datetime.combine(fecha_sel, h_fin)
                if t2 < t1: t2 += timedelta(days=1)
                ht = (t2-t1).seconds/3600
                en, tl = False, "D"
                if turno=="N" or (turno=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)): en, tl = True, "N"
                if comida: ht = max(0, ht-1)
                
                pid = trab_sel.split(" - ")[0]; pnom = trab_sel.split(" - ")[1]
                st.session_state.lista_sel.append({"ID": pid, "Nombre": pnom, "H_Inicio": h_ini.strftime("%H:%M"), "H_Fin": h_fin.strftime("%H:%M"), "Total_Horas": round(ht,2), "Turno_Letra": tl, "Es_Noche": en})
        
        if st.session_state.lista_sel:
            st.markdown("### 📋 Cuadrilla del Día")
            st.dataframe(pd.DataFrame(st.session_state.lista_sel)[["ID", "Nombre", "Total_Horas", "Turno_Letra"]], use_container_width=True)
            if st.button("Borrar Lista"): st.session_state.lista_sel=[]; st.rerun()
            
        st.divider()
        para = st.checkbox("🛑 Paralización")
        d_para = None
        if para:
            cp1, cp2, cp3 = st.columns([1,1,2])
            pi = cp1.time_input("Ini Parada"); pf = cp2.time_input("Fin Parada"); pm = cp3.text_input("Motivo")
            d1, d2 = datetime.combine(hoy, pi), datetime.combine(hoy, pf)
            durp = round((d2-d1).seconds/3600, 2)
            d_para = {"inicio": str(pi), "fin": str(pf), "duracion": durp, "motivo": pm}
            
        if st.button("💾 GUARDAR TODO", type="primary", use_container_width=True):
            if not st.session_state.lista_sel: st.error("Lista vacía")
            elif not veh_sel: st.error("Elige vehículo")
            else:
                with st.spinner("Guardando..."):
                    ok = guardar_parte_en_nube(fecha_sel, st.session_state.lista_sel, veh_sel, d_para, ID_ROSTER_ACTIVO)
                    pdf = generar_pdf_bytes(str(fecha_sel.date()), veh_sel, st.session_state.lista_sel, d_para, st.session_state.prod_dia)
                    nm = f"Parte_{fecha_sel.date()}_{veh_sel}.pdf"
                    
                    try:
                        if "email" in st.secrets: 
                            enviar_email_pdf(pdf, nm, str(fecha_sel.date()), veh_sel)
                            ms = "📧 Email enviado"
                        else: ms = ""
                    except: ms = "⚠️ Error Email"
                    
                    if ok:
                        st.success(f"✅ Guardado. {ms}")
                        st.download_button("📥 PDF", pdf, nm, "application/pdf")
                        st.session_state.lista_sel=[]; st.session_state.prod_dia={}; time.sleep(3); st.rerun()

with tab2:
    st.header("🏗️ Control de Producción")
    conf = cargar_config_prod()
    if not conf: st.warning("⚠️ No se pudo leer 'ARCHIVOS DE PRODUCION'. Revisa en Drive.")
    else:
        tr = st.selectbox("1️⃣ Tramo", list(conf.keys()), index=None, placeholder="Elige Tramo...")
        
        if tr:
            nom_arch = conf.get(tr)
            st.info(f"📂 Archivo: {nom_arch}")
            
            # --- ZONA OPTIMIZADA CON CACHÉ ---
            hjs = obtener_hojas_track_cached(nom_arch)
            
            if hjs is None:
                st.error(f"❌ Error: No puedo conectar con '{nom_arch}'.")
            elif not hjs:
                st.warning("⚠️ El archivo no tiene pestañas 'HR TRACK'.")
            else:
                hj = st.selectbox("2️⃣ Hoja", hjs, index=None, placeholder="Elige Hoja...")
                if hj:
                    sh = conectar_por_nombre_raw(nom_arch)
                    ws = sh.worksheet(hj)
                    col_a = ws.col_values(1)
                    items = [x for x in col_a if x and len(x)>2 and "ITEM" not in x and "HR TRACK" not in x]
                    
                    fil = st.text_input("🔍 Filtro Km (ej: 52)")
                    if fil: items = [i for i in items if fil in i]
                    it = st.selectbox("3️⃣ Elemento", items)
                    
                    if it:
                        r = col_a.index(it)+1
                        st.divider()
                        st.markdown(f"### 📍 {it}")
                        
                        # CIMENTACIÓN
                        c1, c2 = st.columns([1, 2])
                        ec, fc = ws.cell(r, 3).value, ws.cell(r, 5).value
                        c1.info(f"Cim: {ec or '-'}")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("Grabar CIM", key="b_cim"):
                            guardar_produccion(nom_arch, hj, r, 5, datetime.now().strftime("%d/%m/%Y"))
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("CIM"); st.rerun()
                            
                        st.divider()
                        
                        # POSTE
                        c1, c2 = st.columns([1, 2])
                        ep, fp = ws.cell(r, 6).value, ws.cell(r, 8).value
                        c1.info(f"Poste: {ep or '-'}")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("Grabar POSTE", key="b_pos"):
                            guardar_produccion(nom_arch, hj, r, 8, datetime.now().strftime("%d/%m/%Y"))
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("POSTE"); st.rerun()
                            
                        st.divider()
                        
                        # MENSULA
                        c1, c2 = st.columns([1, 2])
                        m_desc = f"{ws.cell(r,33).value or ''} {ws.cell(r,34).value or ''}".strip()
                        fm = ws.cell(r, 38).value
                        c1.info(f"Ménsula: {m_desc or '-'}")
                        if fm: c2.success(f"Hecho: {fm}")
                        elif c2.button("Grabar MENSULA", key="b_men"):
                            guardar_produccion(nom_arch, hj, r, 38, datetime.now().strftime("%d/%m/%Y"))
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("MENSULA"); st.rerun()
                            
                        st.divider()
                        
                        # ANCLAJES
                        st.write("**Anclajes:**")
                        cols_t, cols_f = [18, 21, 24, 27], [20, 23, 26, 29]
                        typs, idxs, done = [], [], False
                        for i in range(4):
                            v = ws.cell(r, cols_t[i]).value
                            if v:
                                typs.append(str(v)); idxs.append(i)
                                if ws.cell(r, cols_f[i]).value: done = True
                        
                        c1, c2 = st.columns([1, 2])
                        c1.info(f"Tipos: {', '.join(typs) if typs else 'Ninguno'}")
                        
                        if not typs: c2.write("-")
                        elif done: c2.success("✅ Ya registrados")
                        elif c2.button("Grabar ANCLAJES", key="b_anc"):
                            hoy = datetime.now().strftime("%d/%m/%Y")
                            for i in idxs: guardar_produccion(nom_arch, hj, r, cols_f[i], hoy)
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("ANCLAJES"); st.rerun()
