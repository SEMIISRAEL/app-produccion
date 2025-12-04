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

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs ---
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#           LOGIN (AUTOMÁTICO)
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = True
    st.session_state.user_name = "Usuario Tablet"
    st.session_state.user_role = "encargado"

# ==========================================
#           CONEXIÓN ROBUSTA
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
    st.markdown("---")
    archivos_roster = buscar_archivos_roster()
    ID_ROSTER_ACTIVO = None
    if archivos_roster:
        # Selección automática del más reciente para no complicar
        nombre_roster_sel = list(archivos_roster.keys())[0]
        ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
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
            # Filtro ya fichado
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

def guardar_prod_con_nota(archivo, hoja, fila, col, valor, vehiculo):
    sh = conectar_flexible(archivo)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja)
        ws.update_cell(fila, col, valor)
        
        # Añadir NOTA
        celda_a1 = rowcol_to_a1(fila, col)
        nota = f"📅 {valor}\n🚛 {vehiculo}\n👷 {st.session_state.user_name}"
        ws.insert_note(celda_a1, nota)
        
        cargar_datos_completos_hoja.clear()
        return True
    except: return False

# ==========================================
#      PDF Y EMAIL
# ==========================================
def generar_pdf(fecha, jefe, lista, para, prod):
    b = BytesIO()
    c = canvas.Canvas(b, pagesize=A4); _, h = A4
    
    start_time, end_time = "________", "________"
    if lista: start_time, end_time = lista[0]['H_Inicio'], lista[0]['H_Fin']

    y = h - 90
    c.setLineWidth(1); c.rect(40, y - 60, 515, 70) 
    c.setFont("Helvetica-Bold", 16); c.drawString(50, h - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10); c.drawString(400, h - 50, "Israel Railways Project")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y - 15, f"Date: {fecha}"); c.drawString(250, y - 15, f"Vehicle / Activity: {jefe}")
    c.drawString(50, y - 45, f"Start Time: {start_time}"); c.drawString(200, y - 45, f"End Time: {end_time}")
    c.drawString(350, y - 45, "Weather: ________")

    y_cursor = y - 80
    c.setFillColor(colors.HexColor("#2980B9")); c.rect(40, y_cursor, 515, 20, fill=1); c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    headers = ["Employee Name", "ID Number", "Company", "Profession", "Normal", "Extra", "Night"]
    x_coords = [40, 180, 260, 330, 400, 450, 500, 555]
    for i, head in enumerate(headers): c.drawString(x_coords[i] + 5, y_cursor + 6, head)
    
    y_cursor -= 20; c.setFillColor(colors.black); c.setFont("Helvetica", 9); y_tabla_start = y - 80
    for t in lista:
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
        if y_cursor < 200: c.showPage(); y_cursor = h - 50
    
    y_min = h - 400
    while y_cursor > y_min: c.setLineWidth(0.5); c.line(40, y_cursor, 555, y_cursor); y_cursor -= 20
    c.setLineWidth(1)
    for x in x_coords: c.line(x, y_tabla_start + 20, x, y_cursor + 20 - 20)
    c.line(555, y_tabla_start + 20, 555, y_cursor) 

    y_bloque = y_cursor
    if para:
        c.setStrokeColor(colors.red); c.setLineWidth(2); c.rect(40, y_bloque - 50, 515, 50)
        c.setFillColor(colors.red); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_bloque - 15, "⚠️ DELAY")
        c.setFillColor(colors.black); c.setFont("Helvetica", 10); c.drawString(50, y_bloque - 35, f"{para['inicio']}-{para['fin']} | {para['motivo']}")
        c.setStrokeColor(colors.black); c.setLineWidth(1); y_bloque -= 70

    y_act = y_bloque; alt = y_act - 130
    if alt > 20:
        c.rect(40, 130, 515, alt); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_act - 15, "Production:")
        yl = y_act - 35
        if prod:
            c.setFont("Helvetica", 9)
            for k,v in prod.items(): c.drawString(50, yl+5, f"- {k}: {','.join(v)}"); c.line(40, yl, 555, yl); yl-=20
        while yl > 135: c.line(40, yl, 555, yl); yl-=20

    c.setLineWidth(1); c.rect(40, 30, 515, 90); c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 100, "Machinery / Materials:"); c.line(40, 70, 555, 70)
    c.drawString(50, 50, "SIGNATURE (ENCARGADO): __________________________")
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
# VARIABLES GLOBALES DE SESIÓN PARA COMPARTIR DATOS ENTRE PESTAÑAS
if 'global_vehiculo' not in st.session_state: st.session_state.global_vehiculo = None
if 'global_tramo' not in st.session_state: st.session_state.global_tramo = None

t1, t2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

with t1:
    st.subheader("Datos Generales")
    if ID_ROSTER_ACTIVO:
        c1, c2, c3, c4, c5 = st.columns([1,1,1,2,2])
        d = c1.selectbox("Día", range(1,32), index=datetime.now().day-1)
        m = c2.selectbox("Mes", range(1,13), index=datetime.now().month-1)
        a = c3.selectbox("Año", [2024,2025,2026], index=1)
        fe = datetime(a,m,d)
        
        vh = cargar_vehiculos_dict()
        ve = c4.selectbox("Vehículo / Lugar", [""]+list(vh.keys()) if vh else [""])
        st.session_state.global_vehiculo = ve # GUARDAMOS PARA USAR EN PROD
        
        info_ve = vh.get(ve, "") if vh else ""
        c5.text_input("Detalle", value=info_ve, disabled=True)
        
        # --- SELECTOR DE TRAMO (MOVIDO AQUÍ) ---
        conf_prod = cargar_config_prod()
        tr_sel = st.selectbox("Seleccionar Tramo / Proyecto", list(conf_prod.keys()) if conf_prod else [], index=None, placeholder="Elige Tramo...")
        st.session_state.global_tramo = tr_sel # GUARDAMOS PARA USAR EN PROD

        st.divider()
        fl = st.radio("Filtro", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        trabs = cargar_trabajadores(ID_ROSTER_ACTIVO)
        if fl=="ALMACEN": fil=[t for t in trabs if t['tipo']=="ALMACEN"]
        elif fl=="OBRA": fil=[t for t in trabs if t['tipo']!="ALMACEN"]
        else: fil=trabs
        
        sel_t = c1.selectbox("Operario", [""]+[t['display'] for t in fil], key="sel_op")
        
        # RESTO DE LA UI DE PARTES
        c_a1, c_a2, c_a3, c_a4 = st.columns(4)
        h_ini = c_a1.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = c_a2.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        turno = c_a3.selectbox("Turno", ["AUT", "D", "N"])
        comida = c_a4.checkbox("-1h Comida", value=(fl=="ALMACEN"))
        
        if st.button("Añadir"):
            if sel_t and sel_t != "":
                t1 = datetime.combine(fe, h_ini); t2 = datetime.combine(fe, h_fin)
                if t2 < t1: t2 += timedelta(days=1)
                ht = (t2-t1).seconds/3600
                en, tl = False, "D"
                if turno=="N" or (turno=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)): en, tl = True, "N"
                if comida: ht = max(0, ht-1)
                pid = sel_t.split(" - ")[0]; pnom = sel_t.split(" - ")[1]
                st.session_state.lista.append({"ID":pid, "Nombre":pnom, "Total_Horas":round(ht,2), "Turno_Letra":tl, "H_Inicio":h_ini.strftime("%H:%M"), "H_Fin":h_fin.strftime("%H:%M"), "Es_Noche":en})
        
        if st.session_state.lista:
            st.dataframe(pd.DataFrame(st.session_state.lista)[["ID","Nombre","Total_Horas"]], use_container_width=True)
            if st.button("Borrar Lista"): st.session_state.lista=[]

        if st.button("GUARDAR PARTE", type="primary"):
            if not st.session_state.lista: st.error("Lista vacía")
            elif not ve: st.error("Falta vehículo")
            else:
                ok = guardar_parte(fe, st.session_state.lista, ve, None, ID_ROSTER_ACTIVO)
                if ok:
                    pdf = generar_pdf(str(fe.date()), ve, st.session_state.lista, None, st.session_state.prod)
                    enviar_email(pdf, f"Parte_{fe.date()}.pdf", str(fe.date()), ve)
                    st.success("Guardado"); st.session_state.lista=[]; st.session_state.prod={}

with t2:
    st.header("🏗️ Control de Producción")
    
    # VERIFICACIÓN DE SEGURIDAD
    veh_actual = st.session_state.global_vehiculo
    tramo_actual = st.session_state.global_tramo
    
    if not veh_actual:
        st.warning("⛔ Selecciona primero el VEHÍCULO en la Pestaña 1.")
    elif not tramo_actual:
        st.warning("⛔ Selecciona primero el TRAMO en la Pestaña 1.")
    else:
        conf = cargar_config_prod()
        nom = conf.get(tramo_actual)
        hjs = obtener_hojas_track_cached(nom)
        
        if hjs:
            hj = st.selectbox("Hoja", hjs, index=None)
            if hj:
                with st.spinner("Cargando..."):
                    datos_completos = cargar_datos_completos_hoja(nom, hj)
                
                if datos_completos:
                    fil = st.text_input("Filtro Km")
                    keys = list(datos_completos.keys())
                    if fil: keys = [k for k in keys if fil in str(k)]
                    it = st.selectbox("Elemento", keys)
                    
                    if it:
                        info = datos_completos[it]
                        fr = info['fila_excel']
                        d = info['datos']
                        
                        st.divider()
                        st.markdown(f"### {it}")
                        
                        c1, c2 = st.columns([1,2])
                        ec, fc = safe_val(d, 3), safe_val(d, 5)
                        c1.info(f"Cim: {ec}")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("Grabar CIM"):
                            # GRABAMOS CON NOTA (VEHÍCULO)
                            guardar_prod_con_nota(nom, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("CIM"); st.rerun()
                            
                        st.divider()
                        c1, c2 = st.columns([1,2])
                        ep, fp = safe_val(d, 6), safe_val(d, 8)
                        c1.info(f"Poste: {ep}")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("Grabar POSTE"):
                            guardar_prod_con_nota(nom, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), veh_actual)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("POSTE"); st.rerun()
                            
                        st.divider()
                        # ANCLAJES (Ejemplo simple)
                        if st.button("Grabar Anclajes"):
                            guardar_prod_con_nota(nom, hj, fr, 20, datetime.now().strftime("%d/%m/%Y"), veh_actual) # Solo graba en la primera col por brevedad
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("ANC"); st.rerun()
