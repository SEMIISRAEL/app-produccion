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
from gspread.utils import rowcol_to_a1 # Necesario para las notas

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs ---
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

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
#      CONEXIÓN ROBUSTA
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
#      SIDEBAR
# ==========================================
@st.cache_data(ttl=300)
def buscar_archivos_roster():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
        service = build('drive', 'v3', credentials=creds)
        results = service.files().list(q="name contains 'Roster' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false", fields="files(id, name)", orderBy="name desc").execute()
        return {f['name']: f['id'] for f in results.get('files', [])}
    except: return {}

with st.sidebar:
    st.write(f"👤 **{st.session_state.user_name}**")
    if st.button("Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()
    st.markdown("---")
    archivos_roster = buscar_archivos_roster()
    ID_ROSTER_ACTIVO = None
    if archivos_roster:
        if st.session_state.user_role == "admin":
            st.header("🗂️ Configuración")
            sel = st.selectbox("Roster:", list(archivos_roster.keys()))
            ID_ROSTER_ACTIVO = archivos_roster[sel]
        else:
            ID_ROSTER_ACTIVO = list(archivos_roster.values())[0]
    else: st.error("No hay Rosters.")

# ==========================================
#      CARGAS DATOS
# ==========================================
@st.cache_data(ttl=600)
def cargar_vehiculos_dict():
    sh = conectar_flexible(ID_VEHICULOS)
    if not sh: return {}
    try:
        return {r[0]: (r[1] if len(r)>1 else "") for r in sh.sheet1.get_all_values() if r and r[0] and "veh" not in r[0].lower()}
    except: return {}

@st.cache_data(ttl=600)
def cargar_trabajadores(id_roster):
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

@st.cache_data(ttl=600)
def cargar_config_prod():
    sh = conectar_flexible(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        return {r[0].strip(): r[1].strip() for r in sh.sheet1.get_all_values() if len(r)>1 and r[0]}
    except: return {}

@st.cache_data(ttl=600)
def obtener_hojas_track_cached(nombre_archivo):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try: return [ws.title for ws in sh.worksheets() if "HR TRACK" in ws.title.upper()]
    except: return []

# ==========================================
#      GUARDADO (CON NOTAS)
# ==========================================
def guardar_parte(fecha, lista, vehiculo, para, id_roster):
    sh = conectar_flexible(id_roster)
    if not sh: return False
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        header = ws.range(f"E4:AX9")
        c_idx = next((c.col for c in header if str(c.value) == str(fecha.day)), 14)
        ids_col = ws.col_values(1)
        upds = []
        for t in lista:
            try: 
                r = ids_col.index(t['ID']) + 1
                upds.append(gspread.Cell(r, c_idx, t['Turno_Letra']))
                upds.append(gspread.Cell(r, c_idx+1, t['Total_Horas']))
            except: pass
        if upds: ws.update_cells(upds)
        if para:
            try: wp = sh.worksheet("Paralizaciones")
            except: wp = sh.add_worksheet("Paralizaciones", 1000, 10)
            wp.append_row([str(fecha.date()), vehiculo, para['inicio'], para['fin'], para['duracion'], para['motivo'], st.session_state.user_name])
        return True
    except: return False

# --- FUNCIÓN MEJORADA CON NOTAS ---
def guardar_prod_con_nota(archivo, hoja, fila, col, valor, vehiculo):
    sh = conectar_flexible(archivo)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja)
        
        # 1. Escribir la fecha
        ws.update_cell(fila, col, valor)
        
        # 2. Añadir la NOTA (Comentario desplegable)
        # Calculamos la dirección A1 (ej: "E15")
        celda_a1 = rowcol_to_a1(fila, col)
        
        hora_actual = datetime.now().strftime("%H:%M")
        usuario = st.session_state.user_name
        
        # Texto que saldrá al pasar el ratón
        nota = f"📅 {valor} - {hora_actual}\n🚛 {vehiculo}\n👷 {usuario}"
        
        ws.insert_note(celda_a1, nota)
        
        # Limpiamos caché
        cargar_datos_completos_hoja.clear()
        return True
    except Exception as e:
        st.error(f"Error nota: {e}")
        return False

# ==========================================
#      PDF Y EMAIL
# ==========================================
def generar_pdf(fecha, jefe, lista, para, prod):
    b = BytesIO()
    c = canvas.Canvas(b, pagesize=A4); _, h = A4
    c.setFont("Helvetica-Bold", 16); c.drawString(50, h-50, "Daily Work Log - SEMI")
    c.setFont("Helvetica", 10); c.drawString(50, h-80, f"Date: {fecha} | Team: {jefe}")
    y = h-120
    for t in lista:
        c.drawString(50, y, f"{t['Nombre']} | {t['Total_Horas']}h"); y-=20
    if para: c.drawString(50, y-20, f"DELAY: {para['motivo']}"); y-=40
    if prod:
        c.drawString(50, y-20, "Production:"); y-=35
        for k,v in prod.items(): c.drawString(60, y, f"{k}: {','.join(v)}"); y-=15
    c.save(); b.seek(0); return b

def enviar_email(pdf, nombre, fecha, jefe):
    try:
        if "email" not in st.secrets: return False
        u, p, d = st.secrets["email"]["usuario"], st.secrets["email"]["password"], st.secrets["email"]["destinatario"]
        msg = MIMEMultipart(); msg['Subject']=f"Parte {fecha} {jefe}"; msg['From']=u; msg['To']=d
        att = MIMEBase('application','octet-stream'); att.set_payload(pdf.getvalue()); encoders.encode_base64(att)
        att.add_header('Content-Disposition',f"attachment; filename={nombre}"); msg.attach(att)
        s = smtplib.SMTP('smtp.gmail.com',587); s.starttls(); s.login(u,p); s.sendmail(u,d,msg.as_string()); s.quit()
        return True
    except: return False

# ==========================================
#           UI
# ==========================================
if 'lista' not in st.session_state: st.session_state.lista = []
if 'prod' not in st.session_state: st.session_state.prod = {}
# Variables globales de sesión para compartir entre pestañas
if 'global_vehiculo' not in st.session_state: st.session_state.global_vehiculo = None
if 'global_tramo' not in st.session_state: st.session_state.global_tramo = None

t1, t2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

with t1:
    # --- SELECCIÓN GLOBAL (TRAMO + VEHÍCULO) ---
    st.subheader("Configuración del Turno")
    
    if ID_ROSTER_ACTIVO:
        c1, c2, c3, c4 = st.columns([1,1,1,2])
        d = c1.selectbox("Día", range(1,32), index=datetime.now().day-1)
        m = c2.selectbox("Mes", range(1,13), index=datetime.now().month-1)
        a = c3.selectbox("Año", [2024,2025,2026], index=1)
        fe = datetime(a,m,d)
        
        # Carga de Vehículos
        vh = cargar_vehiculos_dict()
        # Guardamos en session_state
        opc_v = [""]+list(vh.keys()) if vh else [""]
        ve = c4.selectbox("Vehículo / Lugar", opc_v)
        st.session_state.global_vehiculo = ve # GUARDAR PARA PESTAÑA 2
        
        # Carga de Tramos (MOVIDO AQUI)
        conf_prod = cargar_config_prod()
        tr_sel = st.selectbox("Seleccionar Tramo / Proyecto", list(conf_prod.keys()) if conf_prod else [], index=None, placeholder="Elige Tramo...")
        st.session_state.global_tramo = tr_sel # GUARDAR PARA PESTAÑA 2
        
        st.divider()
        
        # --- AÑADIR PERSONAL ---
        fl = st.radio("Filtro", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        trabs = cargar_trabajadores(ID_ROSTER_ACTIVO)
        if fl=="ALMACEN": fil=[t for t in trabs if t['tipo']=="ALMACEN"]
        elif fl=="OBRA": fil=[t for t in trabs if t['tipo']!="ALMACEN"]
        else: fil=trabs
        
        c_a1, c_a2 = st.columns([3,1])
        sel_t = c_a1.selectbox("Operario", [""]+[t['display'] for t in fil])
        h_ini = c_a2.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        
        if st.button("Añadir"):
            if sel_t: 
                parts = sel_t.split(" - ")
                st.session_state.lista.append({"ID":parts[0], "Nombre":parts[1], "Total_Horas":8, "Turno_Letra":"D", "H_Inicio":str(h_ini), "H_Fin":"16:00", "Es_Noche":False})
        
        if st.session_state.lista:
            st.dataframe(pd.DataFrame(st.session_state.lista)[["ID","Nombre"]])
            if st.button("Borrar"): st.session_state.lista=[]
            
        if st.button("GUARDAR PARTE", type="primary"):
            ok = guardar_parte(fe, st.session_state.lista, ve, None, ID_ROSTER_ACTIVO)
            if ok:
                pdf = generar_pdf(str(fe.date()), ve, st.session_state.lista, None, st.session_state.prod)
                enviar_email(pdf, f"Parte_{fe.date()}.pdf", str(fe.date()), ve)
                st.success("Guardado"); st.session_state.lista=[]; st.session_state.prod={}

with t2:
    st.header("🏗️ Control de Producción")
    
    # --- VERIFICACIÓN DE SEGURIDAD ---
    # Si no han elegido Vehículo o Tramo en la pestaña 1, bloqueamos
    veh_actual = st.session_state.global_vehiculo
    tramo_actual = st.session_state.global_tramo
    
    if not veh_actual:
        st.error("⛔ DEBES SELECCIONAR UN VEHÍCULO EN LA PESTAÑA 'PARTES' PRIMERO.")
    elif not tramo_actual:
        st.error("⛔ DEBES SELECCIONAR UN TRAMO EN LA PESTAÑA 'PARTES' PRIMERO.")
    else:
        # SI TODO ESTÁ OK, CARGAMOS EL ARCHIVO DEL TRAMO ELEGIDO
        conf = cargar_config_prod()
        nom = conf.get(tramo_actual)
        st.success(f"Trabajando en: **{tramo_actual}** | Equipo: **{veh_actual}**")
        
        hjs = obtener_hojas_track_cached(nom)
        if hjs:
            hj = st.selectbox("Hoja", hjs, index=None)
            if hj:
                with st.spinner("Cargando datos..."):
                    datos_completos = cargar_datos_completos_hoja(nom, hj)
                
                if datos_completos:
                    fil = st.text_input("Filtro Km")
                    keys = list(datos_completos.keys())
                    if fil: keys = [k for k in keys if fil in str(k)]
                    it = st.selectbox("Elemento", keys)
                    
                    if it:
                        info = datos_completos[it]
                        fila_real = info['fila_excel']
                        datos = info['datos']
                        
                        st.divider()
                        st.markdown(f"### 📍 {it}")
                        
                        # CIMENTACIÓN
                        c1, c2 = st.columns([1,2])
                        ec, fc = safe_val(datos, 3), safe_val(datos, 5)
                        c1.info(f"Cim: {ec}")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("Grabar CIM"):
                            # AQUÍ USAMOS LA NUEVA FUNCIÓN CON NOTAS
                            guardar_prod_con_nota(nom, hj, fila_real, 5, datetime.now().strftime("%d/%m/%Y"), veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("CIM"); st.rerun()
                            
                        st.divider()
                        # POSTE
                        c1, c2 = st.columns([1,2])
                        ep, fp = safe_val(datos, 6), safe_val(datos, 8)
                        c1.info(f"Poste: {ep}")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("Grabar POSTE"):
                            guardar_prod_con_nota(nom, hj, fila_real, 8, datetime.now().strftime("%d/%m/%Y"), veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("POSTE"); st.rerun()
                            
                        st.divider()
                        # MENSULA
                        c1, c2 = st.columns([1,2])
                        m = f"{safe_val(datos,33) or ''} {safe_val(datos,34) or ''}".strip()
                        fm = safe_val(datos, 38)
                        c1.info(f"Ménsula: {m or '-'}")
                        if fm: c2.success(f"Hecho: {fm}")
                        elif c2.button("Grabar MENSULA"):
                            guardar_prod_con_nota(nom, hj, fila_real, 38, datetime.now().strftime("%d/%m/%Y"), veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("MEN"); st.rerun()
                        
                        # ANCLAJES (Se graban igual pero solo nota en la primera columna por simplicidad)
                        st.divider(); st.write("**Anclajes**")
                        idx_t = [18,21,24,27]; idx_f = [20,23,26,29]
                        typs, grp_f, done = [], [], False
                        for i in range(4):
                            v = safe_val(datos, idx_t[i])
                            if v:
                                typs.append(v); grp_f.append(idx_f[i])
                                if safe_val(datos, idx_f[i]): done=True
                        c1, c2 = st.columns([1,2])
                        c1.info(", ".join(typs) if typs else "-")
                        if not typs: c2.write("-")
                        elif done: c2.success("Hecho")
                        elif c2.button("Grabar ANCLAJES"):
                            hoy = datetime.now().strftime("%d/%m/%Y")
                            for c in grp_f: 
                                guardar_prod_con_nota(nom, hj, fila_real, c, hoy, veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("ANC"); st.rerun()
