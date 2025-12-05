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

# --- IDs FIJOS ---
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#           LOGIN AUTOMÁTICO
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = True
    st.session_state.user_name = "Usuario Tablet"
    st.session_state.user_role = "encargado"

# Variables globales de sesión
if 'ID_ROSTER' not in st.session_state: st.session_state.ID_ROSTER = None
if 'TRAMO_ACTIVO' not in st.session_state: st.session_state.TRAMO_ACTIVO = None
if 'ARCH_PROD' not in st.session_state: st.session_state.ARCH_PROD = None
if 'ARCH_BACKUP' not in st.session_state: st.session_state.ARCH_BACKUP = None
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

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
            try: return client.open(referencia.replace(".xlsx", "").strip())
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
#      SIDEBAR (CONFIGURACIÓN IZQUIERDA)
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
    
    # 1. ROSTER
    st.caption("📅 ROSTER ACTIVO")
    archivos_roster = buscar_archivos_roster()
    
    if archivos_roster:
        if st.session_state.user_role == "admin":
            st.header("🗂️ Configuración")
            nombre_roster_sel = st.selectbox("Archivo Horas:", list(archivos_roster.keys()))
        else:
            nombre_roster_sel = list(archivos_roster.keys())[0]
            
        st.session_state.ID_ROSTER = archivos_roster[nombre_roster_sel]
        st.success(f"Conectado: {nombre_roster_sel}")
    else: st.error("No hay Rosters.")
    
    st.markdown("---")

    # 2. TRAMO
    st.caption("🏗️ PROYECTO / TRAMO")
    conf_prod = cargar_config_prod()
    
    if conf_prod:
        tramo_sel = st.selectbox("Seleccionar Tramo:", list(conf_prod.keys()), index=None, placeholder="Elige...")
        if tramo_sel:
            st.session_state.TRAMO_ACTIVO = tramo_sel
            st.session_state.ARCH_PROD, st.session_state.ARCH_BACKUP = conf_prod.get(tramo_sel)
            st.info(f"📁 {st.session_state.ARCH_PROD}")
    else:
        st.warning("Sin config producción")

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
def obtener_hojas_track_cached(nombre_archivo):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try: return [ws.title for ws in sh.worksheets() if "HR TRACK" in ws.title.upper()]
    except: return []

# ==========================================
#      GUARDADO Y EMAIL
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
        st.error(f"❌ Error Principal: {e}")
        return False
        
    if archivo_backup and archivo_backup != "":
        try:
            sh_bk = conectar_flexible(archivo_backup)
            if sh_bk:
                ws_bk = sh_bk.worksheet(hoja)
                ws_bk.update_cell(fila, col, valor)
                ws_bk.insert_note(rowcol_to_a1(fila, col), nota)
                st.toast("Copia guardada")
        except Exception as e:
            st.warning(f"⚠️ Falló el Backup: {e}")

    cargar_datos_completos_hoja.clear() 
    return exito_principal

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
    c.drawString(50, y - 15, f"Date: {fecha}"); c.drawString(250, y - 15, f"Vehicle / Location: {jefe}")
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
    for x in x_coords: c.line(x, y_tabla_start + 20, x, y_final - 20)
    c.line(555, y_tabla_start + 20, 555, y_final - 20) 

    y_bloque = y_final - 40
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
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None

t1, t2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

with t1:
    st.subheader("Datos Generales")
    if st.session_state.ID_ROSTER:
        c1, c2, c3, c4, c5 = st.columns([1,1,1,2,2])
        hoy = datetime.now()
        d = c1.selectbox("Día", range(1,32), index=hoy.day-1)
        m = c2.selectbox("Mes", range(1,13), index=hoy.month-1)
        a = c3.selectbox("Año", [2024,2025,2026], index=1)
        try: fecha_sel = datetime(a,m,d)
        except: fecha_sel = hoy; st.error("Fecha incorrecta")

        dv = cargar_vehiculos_dict()
        nv = [""] + list(dv.keys()) if dv else ["Error"]
        ve = c4.selectbox("Vehículo / Lugar", nv)
        st.session_state.veh_glob = ve
        c5.text_input("Detalle", value=dv.get(ve, "") if dv else "", disabled=True)
        
        st.divider()
        fl = st.radio("Filtro:", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        trabs = cargar_trabajadores(st.session_state.ID_ROSTER)
        if fl=="ALMACEN": fil = [t for t in trabs if t['tipo']=="ALMACEN"]; def_com=True
        elif fl=="OBRA": fil = [t for t in trabs if t['tipo']!="ALMACEN"]; def_com=False
        else: fil = trabs; def_com=False
            
        opc = [""] + [t['display'] for t in fil] if fil else ["Sin personal disponible"]
        trab_sel = st.selectbox("Seleccionar Operario", opc)
        
        ch1, ch2, ch3, ch4 = st.columns(4)
        h_ini = ch1.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = ch2.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        turno = ch3.selectbox("Turno", ["AUT", "D", "N"])
        comida = ch4.checkbox("-1h Comida", value=def_com)
        
        if st.button("➕ AÑADIR", type="secondary", use_container_width=True):
            if trab_sel and trab_sel != "" and trab_sel != "Sin personal disponible":
                t1 = datetime.combine(fecha_sel, h_ini); t2 = datetime.combine(fecha_sel, h_fin)
                if t2 < t1: t2 += timedelta(days=1)
                ht = (t2-t1).seconds/3600
                en, tl = False, "D"
                if turno=="N" or (turno=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)): en, tl = True, "N"
                if comida: ht = max(0, ht-1)
                pid = trab_sel.split(" - ")[0]; pnom = trab_sel.split(" - ")[1]
                st.session_state.lista_sel.append({"ID":pid, "Nombre":pnom, "Total_Horas":round(ht,2), "Turno_Letra":tl, "H_Inicio":h_ini.strftime("%H:%M"), "H_Fin":h_fin.strftime("%H:%M"), "Es_Noche":en})
        
        if st.session_state.lista_sel:
            st.markdown("### 📋 Cuadrilla del Día")
            st.dataframe(pd.DataFrame(st.session_state.lista_sel)[["ID","Nombre","Total_Horas","Turno_Letra"]], use_container_width=True)
            if st.button("Borrar Lista"): st.session_state.lista_sel=[]; st.rerun()
            
        st.markdown("---")
        para = st.checkbox("🛑 Registrar Paralización")
        d_para = None
        if para:
            cp1, cp2, cp3 = st.columns([1,1,2])
            pi = cp1.time_input("Ini Parada"); pf = cp2.time_input("Fin Parada"); pm = cp3.text_input("Motivo")
            d1, d2 = datetime.combine(hoy, pi), datetime.combine(hoy, pf)
            durp = round((d2-d1).seconds/3600, 2)
            d_para = {"inicio": str(pi), "fin": str(pf), "duracion": durp, "motivo": pm}
            
        if st.button("💾 GUARDAR TODO", type="primary", use_container_width=True):
            if not st.session_state.lista_sel: st.error("Lista vacía")
            elif not ve: st.error("Elige vehículo")
            else:
                with st.spinner("Guardando..."):
                    ok = guardar_parte(fecha_sel, st.session_state.lista_sel, ve, d_para, st.session_state.ID_ROSTER)
                    pdf = generar_pdf(str(fecha_sel.date()), ve, st.session_state.lista_sel, d_para, st.session_state.prod_dia)
                    nm = f"Parte_{fecha_sel.date()}_{ve}.pdf"
                    try:
                        if "email" in st.secrets: 
                            enviar_email(pdf, nm, str(fecha_sel.date()), ve)
                            ms = "📧 Email enviado"
                        else: ms = ""
                    except: ms = "⚠️ Error Email"
                    if ok:
                        st.success(f"✅ Guardado. {ms}")
                        st.download_button("📥 PDF", pdf, nm, "application/pdf")
                        st.session_state.lista_sel=[]; st.session_state.prod_dia={}; time.sleep(3); st.rerun()

with t2:
    if not st.session_state.veh_glob: st.warning("⛔ Elige Vehículo en Pestaña 1")
    elif not st.session_state.TRAMO_ACTIVO: st.warning("⛔ Elige Tramo en menú lateral")
    else:
        nom = st.session_state.ARCH_PROD
        bk = st.session_state.ARCH_BACKUP
        hjs = obtener_hojas_track_cached(nom)
        if hjs:
            hj = st.selectbox("Hoja", hjs, index=None)
            if hj:
                with st.spinner("Cargando..."):
                    datos_completos = cargar_datos_completos_hoja(nom, hj)
                
                if datos_completos:
                    # 1. PREPARAMOS LAS LISTAS DE FILTROS
                    todos_los_items = datos_completos.values()
                    
                    # Cimentaciones (Col 2 -> índice C)
                    list_cim = sorted(list(set(d['datos'][2] for d in todos_los_items if len(d['datos'])>2 and d['datos'][2])))
                    # Postes (Col 5 -> índice F)
                    list_post = sorted(list(set(d['datos'][5] for d in todos_los_items if len(d['datos'])>5 and d['datos'][5])))
                    # Anclajes (Cols 17,20,23,26 -> R,U,X,AA)
                    set_anc = set()
                    for d in todos_los_items:
                        row = d['datos']
                        for idx in [17, 20, 23, 26]:
                            if len(row) > idx and row[idx]: set_anc.add(row[idx])
                    list_anc = sorted(list(set_anc))

                    # 2. MOSTRAR FILTROS
                    c_f1, c_f2, c_f3 = st.columns(3)
                    fil_cim = c_f1.selectbox("Filtro Cimentación", ["Todos"] + list_cim)
                    fil_post = c_f2.selectbox("Filtro Poste", ["Todos"] + list_post)
                    fil_anc = c_f3.selectbox("Filtro Anclaje", ["Todos"] + list_anc)
                    
                    fil_km = st.text_input("Filtro Km (ej: 52)")

                    # 3. APLICAR FILTROS
                    keys_filtradas = []
                    for k, info in datos_completos.items():
                        row = info['datos']
                        # Km
                        if fil_km and fil_km not in str(k): continue
                        # Cim
                        if fil_cim != "Todos":
                            val_c = row[2] if len(row)>2 else ""
                            if val_c != fil_cim: continue
                        # Poste
                        if fil_post != "Todos":
                            val_p = row[5] if len(row)>5 else ""
                            if val_p != fil_post: continue
                        # Anclaje
                        if fil_anc != "Todos":
                            vals_a = [row[i] for i in [17,20,23,26] if len(row)>i]
                            if fil_anc not in vals_a: continue
                        
                        keys_filtradas.append(k)

                    it = st.selectbox("Elemento", keys_filtradas)
                    
                    if it:
                        info = datos_completos[it]
                        fr = info['fila_excel']
                        d = info['datos']
                        st.divider()
                        st.markdown(f"### 📍 {it}")
                        
                        c1, c2 = st.columns([1, 2])
                        ec, fc = safe_val(d, 3), safe_val(d, 5)
                        c1.info(f"Cim: {ec}")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("Grabar CIM"):
                            guardar_prod_con_nota(nom, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("CIM"); st.rerun()
                        st.divider()
                        c1, c2 = st.columns([1, 2])
                        ep, fp = safe_val(d, 6), safe_val(d, 8)
                        c1.info(f"Poste: {ep}")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("Grabar POSTE"):
                            guardar_prod_con_nota(nom, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("POSTE"); st.rerun()
                        st.divider()
                        c1, c2 = st.columns([1, 2])
                        m_desc = f"{safe_val(d,32) or ''} {safe_val(d,33) or ''}".strip()
                        fm = safe_val(d, 38)
                        c1.info(f"Ménsula: {m_desc or '-'}")
                        if fm: c2.success(f"Hecho: {fm}")
                        elif c2.button("Grabar MENSULA"):
                            guardar_prod_con_nota(nom, hj, fr, 38, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("MEN"); st.rerun()
                        st.divider()
                        st.write("**Anclajes:**")
                        cols_t, cols_f = [18, 21, 24, 27], [20, 23, 26, 29]
                        typs, cols_escritura, done = [], [], False
                        for i in range(4):
                            v = safe_val(d, cols_t[i])
                            if v:
                                typs.append(str(v)); cols_escritura.append(cols_f[i])
                                if safe_val(d, cols_f[i]): done = True
                        c1, c2 = st.columns([1, 2])
                        c1.info(f"Tipos: {', '.join(typs) if typs else 'Ninguno'}")
                        if not typs: c2.write("-")
                        elif done: c2.success("✅ Ya registrados")
                        elif c2.button("Grabar ANCLAJES"):
                            hoy = datetime.now().strftime("%d/%m/%Y")
                            for c_idx in cols_escritura:
                                guardar_prod_con_nota(nom, hj, fr, c_idx, hoy, st.session_state.veh_glob, bk)
                            if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                            st.session_state.prod_dia[it].append("ANC"); st.rerun()
